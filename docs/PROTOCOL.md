# eufy Smart Lock Touch T8510 — BLE protocol (bridgeless)

Wire-level description of the lock's BLE protocol, **verified working end-to-end** (real unlock, response `0x00`,
state-change notify). Reference implementation: `custom_components/eufy_lock_ble/eufy_ble/` (`client.py`, `protocol.py`, `crypto.py`). This document is the wire-level spec implemented by the integration (usable by any BLE central). Nothing here needs the eufy cloud at runtime — only the per-device secrets
below, which were fetched once from the account.

---

## 1. Device / GATT

- Advertises `T8510_XXXXXXXX`, BLE address `AA:BB:CC:DD:EE:FF` (little-endian of `bt_mac AABBCCDDEEFF`).
- GATT service **`0000FFE0-...`**, characteristic **`0000FFE1-...`** (write-without-response + notify) — the command pipe. Also enable notify on **`0000B003-...`**.
- Only ONE BLE central may connect at a time (close the eufy app first).
- MTU 247 negotiated; frames here are ≤ 202 bytes so a single write/notification.

## 2. Per-device secrets (fetched once from the eufy account, see `fetch_ids.mjs`)

Fetched by the config flow from the eufy account (or with `tools/fetch_ids.mjs`):

| field | value |
|---|---|
| `serial` (full SN) | `T8510Q15XXXXXXXX` |
| `admin_user_id` | `<40 hex>` (40 hex) |
| `short_user_id` | `0` |
| `nickname_masked` | `abc***` |
| `lock_public_key` | `<128 hex>` (128 hex = P-256 X‖Y, the lock's STATIC pubkey) |

The lock static pubkey comes from the cloud endpoint `GET v1/app/public_key/query?device_sn=<SN>&type=2`.

## 3. Frame format (on FFE1)

```
FF 09 | size (u16 LE) | 0x11 | dir | cmd | 0xC0 | body | xor8
```
- `size` = total frame length **including** the FF09 header and the trailing xor byte.
- `dir` = `0xFF` for phone→lock (requests), `0x32` for lock→phone (responses/notifies).
- `xor8` = XOR of every byte before it.
- `cmd`: `0x02` verify, `0x03` heartbeat, `0x08` ON_OFF, `0x11` query-status, `0x12` state-notify (lock→phone push), `0x21` get-params.

## 4. Crypto primitives (`eufy_crypto.py`, all byte-verified)

- **ECDH** curve `prime256v1` / P-256.
- **KDF** `eufyKDF(shared)` → 48 bytes: static `"ECIES"`, two rounds of HMAC-SHA256 keyed by the ECDH shared X-coord (see `eufy_kdf`).
- **ECIES(data → peer_pubkey)** returns:
  `comp_ephemeral_pub(33)  ||  rand(16)  ||  AES128CBC( PKCS7(data), dk[0:16], iv=rand )  ||  HMAC_SHA256( dk[16:48], rand||enc )(32)`
  where `dk = eufyKDF(ECDH(ephemeral_priv, peer_pub))`.  Decryption is the inverse (recompute dk from `ECDH(recipient_priv, comp)`, check HMAC, AES-decrypt).
- **Session cipher** (all commands after the handshake): `AES-128-CBC` + PKCS7, key = the session key **K**, **IV = the serial as ASCII** (`"T8510Q15XXXXXXXX"` = `<serial ascii as hex>`).

## 5. Handshake (establish the session key K)

1. Central generates a **session** P-256 keypair `(sPriv, sPub)` (sPub as raw X‖Y, 64 bytes).
2. Build the **identity** payload (TLV):
   ```
   A1 14 <admin_user_id as 20 RAW bytes>   ← bytes.fromhex(admin_user_id), NOT the 40-char ascii
   A2 07 <YY MM DD hh mm ss timezone_byte> ← LOCAL time now; YY=year-2000
   A3 40 <sPub X‖Y, 64 bytes>
   A4 04 <negative local UTC offset, i32 LE> ← timezone seconds, NOT app flags
   ```
   **The A2 timestamp MUST be fresh** — a stale one still yields a session but the lock refuses to
   actuate (ON_OFF returns `0x42` instead of `0x00`). This was the one non-obvious gotcha.
3. `VERIFY` frame = `frame(0x02, ECIES(identity → lock_public_key))`.  Write to FFE1.
4. Lock replies (`cmd 0x02, dir 0x32`) with `ECIES( … → sPub )`. Decrypt with `sPriv`:
   ```
   00  A1 10 <SESSION KEY K, 16 bytes>  A2 06 <bt_mac>  A3 0a <fw "2.0.4.2">  A4 10 <serial>  A5 05 <hw "1.2">
   ```
   **The lock generates K** and hands it back inside its ECIES response. Take K = the A1 field.
5. From here every command body = `AES128CBC( PKCS7(TLV), K, IV=serial-ascii )`, framed with the right `cmd`.

## 6. Commands (after handshake, all AES with K + serial-IV)

### ON_OFF (cmd 0x08) — lock / unlock
TLV:
```
A1 02 <short_user_id u16 BE = 0x0000>
A2 01 <00 = UNLOCK | 01 = LOCK>
A3 04 <epoch u32 LE>
A4 06 <nickname "abc***" = 6162632a2a2a>
```
Response (`cmd 0x08, dir 0x32`), decrypt with K: **`00` = success**, `0x42` = refused.
An inconsistent identity timezone or stale time can cause a refusal; the complete
set of firmware refusal conditions is not established.
A successful actuation is also followed by an unsolicited **state-notify** (below).

### QUERY_STATUS (cmd 0x11) — is it open or closed
Request TLV = the identity-style `A1 14 <admin 20 raw> A2 04 <epoch u32 LE>`.
Response `00 A1 01 <battery %> A2 01 <state>`: **`A1` = battery level in percent** (0x0a = 10 %), **`A2 = 04` → LOCKED (closed)**, **`03` → UNLOCKED (open)**.

### STATE NOTIFY (cmd 0x12, dir 0x32, unsolicited)
Lock pushes `00 A1 01 <battery %> A2 01 <state>` whenever the bolt changes — same encoding as QUERY_STATUS.
Subscribe to FFE1 notifications and watch these for live open/closed in Home Assistant.

### GET_LOCK_PARAM (cmd 0x21) — settings
Request TLV (identity-style, same as status). Response (sample):
```
00 A1 01 02  A2 01 00  A3 02 3c00  A4 01 00  A5 02 1700  A6 02 0600
   A7 01 01  A8 01 01  A9 02 b400  AA 01 05  AB 01 01  AC 01 00
```
| tag | meaning | sample |
|---|---|---|
| A1 | sound volume (0 silent, 1 low, 2 medium, 3 high) | 2 |
| A2 | auto-lock enabled | 0 |
| A3 | auto-lock delay, u16 LE seconds | 60 |
| A4 | auto-lock schedule enabled | 0 |
| A5 | schedule start, `HH MM` bytes | 23:00 |
| A6 | schedule end, `HH MM` bytes | 06:00 |
| A7 | one-touch lock | 1 |
| A8 | wrong-try protection enabled | 1 |
| A9 | wrong-try lockdown, u16 LE seconds | 180 |
| AA | wrong-try attempts | 5 |
| AB | scramble passcode | 1 |
| AC | unknown | 0 |

Battery is **not** in this block; it is the `A1` byte of QUERY_STATUS / STATE NOTIFY.
`SET_LOCK_PARAM` is cmd `0x22`; its payload has not been verified on hardware.

### HEARTBEAT (cmd 0x03)
`00`-payload keepalive every ~15 s; TLV the same `A1 14 <admin> A2 04 <epoch>` TLV as QUERY_STATUS. Not required to actuate but keeps the session alive.

## 7. Implementation notes

- One `LockSession` per BLE connection: enable notify on FFE1 (+B003), run the handshake, keep K for the
  life of the connection, send heartbeats every ~15 s, decode 0x12 pushes for live state.
- Regenerate a fresh session keypair per connection; build the A2 timestamp from Home Assistant's local
  time at handshake time (the lock's clock was set by the phone in the same time zone).
- The lock advertises intermittently and BLE is weak at range; the HA Bluetooth proxy must be within a
  few metres of the door. Only one central at a time — don't run the eufy app against it simultaneously.


## Timezone correction (0.2.0)

The identity calendar uses Home Assistant local time. The final A2 byte is
`abs(UTC offset in seconds) // 3600`, plus `0x80` when the offset is negative.
A4 is the **negative** local UTC offset in seconds, encoded as a signed 32-bit
little-endian integer. The old `84` and `40380000` values represent UTC−4 only.
For UTC−3 the fields are `83` and `302a0000`; for UTC they are zero.
Fractional offsets retain full seconds in A4; the A2 hour component follows
the integer truncation used by the app. Calculate again at each handshake
so daylight-saving rules follow the configured IANA timezone.

These encodings were compared with the official Android app logic and exercised
on a T8510 with firmware 2.0.2.3. Hardware lock/unlock and successful reads were
confirmed. A `0x42` result is a refusal; the exact firmware conditions for every
refusal have not been exhaustively established. All values and addresses in
this document are synthetic examples or generic protocol constants.
