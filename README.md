# eufy Smart Lock Touch T8510 — Bluetooth for Home Assistant

[Español](README.es.md) · [Supported model](docs/SUPPORTED_MODELS.md) · [Privacy](PRIVACY.md)

Control and monitor **eufy Bluetooth smart locks** directly from Home Assistant through an
**ESPHome Bluetooth proxy** or a local Bluetooth adapter. No eufy Wi-Fi bridge, no cloud at
runtime: the only cloud access is a one-time login during setup to fetch the lock's secrets.

Hardware-tested on the **Smart Lock Touch T8510**, firmware **2.0.2.3**.
Other T85xx locks may be discovered, but their protocol has not been verified.

The lock's BLE protocol is documented in [docs/PROTOCOL.md](docs/PROTOCOL.md). The
integration implements it in pure Python (`custom_components/eufy_lock_ble/eufy_ble/`) and
ships a test suite with a software model of the lock, so the whole integration is exercised
end to end without hardware.

## Identify the supported lock first

**Hardware-tested: eufy Smart Lock Touch, model T8510, firmware 2.0.2.3.**
Lock, unlock, status, battery, settings reads and connection keep-alive were verified.
The upstream project also reports T8510 firmware 2.0.4.2; that is a separate report.
Other models and suffix variants remain unverified.

This Bluetooth model uses **four AA batteries** and has **no built-in Wi-Fi**.
The reference version was sold **without a Wi-Fi bridge**; the bridge was available
separately. Bundles with a bridge also existed. This integration does not require it.

[![Manufacturer product image: exterior and interior of the T8510 Smart Lock Touch](https://cdn.shopify.com/s/files/1/0504/7094/4954/products/E130SmartLock.png?v=1698303947)](https://www.eufy.com/products/t8510)

Image: eufy/Anker, from its T8510 product page (also titled E130). The model code
on the product label is the reference. An E130 name or a similar-looking case
does not by itself establish compatibility. [Model details and sources](docs/SUPPORTED_MODELS.md).

## What you get

| Entity | Type | Notes |
|---|---|---|
| `lock.<name>` | lock | `locked` / `unlocked`, lock and unlock actions. Attributes: `connected`, `rssi`, `last_state_change`. |
| `sensor.<name>_battery` | battery, % | reported by the lock with every status read and every state push |
| `binary_sensor.<name>_connected` | connectivity | on while the BLE session is up; a drop only shows after 60 s down (attributes: raw `session_open`, `connected_since`, `disconnects`) |
| `sensor.<name>_auto_lock_delay` | duration, s | auto-lock delay |
| `binary_sensor.<name>_auto_lock` | | auto-lock enabled |
| `binary_sensor.<name>_auto_lock_schedule` | | auto-lock schedule enabled |
| `sensor.<name>_auto_lock_schedule_start` / `_end` | HH:MM | schedule window |
| `binary_sensor.<name>_one_touch_lock` | | one-touch lock |
| `binary_sensor.<name>_wrong_try_protect` | | wrong-try protection enabled |
| `sensor.<name>_wrong_try_attempts` | | attempts before lockdown |
| `sensor.<name>_wrong_try_lockdown` | duration, s | lockdown time |
| `binary_sensor.<name>_scramble_passcode` | | scramble passcode |
| `sensor.<name>_sound_volume` | enum | silent / low / medium / high |
| `sensor.<name>_signal_strength` | dBm, disabled by default | RSSI as seen by the closest scanner |
| `sensor.<name>_parameters` | disabled by default | the raw settings block, one attribute per field |

All settings entities are **read-only** for now (see *Limitations*). Everything except the
lock itself is in the diagnostic category.

Live updates: in the default **stay connected** mode the lock pushes every bolt change
(thumb-turn, keypad, fingerprint, app) to Home Assistant within about two seconds, together
with the battery level.

## Requirements

- Home Assistant 2026.2.3 or newer with the Bluetooth integration. This is the
  declared support baseline; hardware verification used 2026.10.1.
- A connectable Bluetooth source within a few metres of the door: an
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy) with
  `active: true`, or a local adapter. The lock's radio is weak; at −90 dBm and below the
  link drops regularly.
- The per-lock secrets described in the next section (fetched for you during setup).
- Home Assistant's clock and **time zone** must be correct and match the phone that set up
  the lock. The integration derives **both** identity timezone fields automatically from Home Assistant at each connection, including daylight-saving changes. No country or numeric UTC offset is hard-coded and no manual offset option is needed. Command epoch timestamps remain UTC. A wrong system clock or inconsistent identity fields can lead to a `0x42` rejection; that code is not an exclusive diagnosis of clock skew.

The lock accepts **one Bluetooth central at a time**. While Home Assistant is connected the
eufy app cannot reach the lock (switch the integration to on-demand mode if you need the
app regularly).

## The lock's secrets

Talking to the lock needs a few values tied to your eufy account and the lock: the
16-character serial number, the account's 40-hex *admin user ID*, the lock's static
P-256 public key, the *short user ID* (usually 0) and the masked account nickname the
lock records in its log (e.g. `abc***`).

**The setup flow fetches all of them for you**: enter your eufy e-mail, password and
account country and Home Assistant logs into the eufy cloud once, picks the lock whose
Bluetooth MAC matches the device you are adding (or asks which one), and pre-fills the
review form. Captcha and e-mailed verification codes are handled in the flow. The
password is used for that single login and is **not stored**; the config entry only
keeps the per-lock values above.

If you prefer not to type your eufy password into Home Assistant, tick *Enter the lock
credentials manually instead* and use the standalone Node script:

```bash
cd tools
npm install
EUFY_EMAIL=you@example.com EUFY_PASSWORD='...' EUFY_COUNTRY=YOUR_ACCOUNT_COUNTRY EUFY_LOCK_SN=T8510Q15XXXXXXXX node fetch_ids.mjs
```

It prints a JSON object for the selected lock with `serial`, `admin_user_id`,
`short_user_id`, `lock_public_key` and `nickname`. The helper does not dump the
account inventory or write a `fetch_out.json` file.

Keep these private: together with physical proximity they are enough to operate the lock.

## Installation

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dgg006&repository=ha-eufy-lock-ble&category=integration)

With HACS installed, click the button, confirm the repository and download
**eufy Smart Lock (BLE)**. Restart Home Assistant, then add the integration
from **Settings → Devices & services**.

This is a HACS **custom repository**. Installation by custom URL and inclusion in
the default HACS catalog are separate steps; catalog acceptance is not claimed.

Do not install this fork alongside the upstream integration: both use the same
`eufy_lock_ble` domain. [Migration and release procedure](docs/PUBLISHING.md).


**HACS:** add `https://github.com/dgg006/ha-eufy-lock-ble` as a custom repository (category *Integration*), install
*eufy Smart Lock (BLE)*, restart Home Assistant.

**Manual:** copy `custom_components/eufy_lock_ble` into your `config/custom_components/`
folder and restart. Do not leave backup copies of the folder inside `custom_components/`;
Home Assistant's loader picks up their manifest and refuses to load the integration.

Any update of the files requires a **restart of Home Assistant core**; reloading the entry
is not enough for Python to pick up new code.

## Setup

1. Close the eufy app.
2. Home Assistant discovers the lock (its BLE name starts with `T85`) and offers to set it
   up; otherwise go to *Settings → Devices & services → Add integration → eufy Smart Lock (BLE)*
   and pick it from the list or enter its Bluetooth address.
3. Log into your eufy account (or choose manual entry), review the pre-filled secrets and
   submit. The integration connects, performs the handshake and reads the bolt state to
   validate them. Tick *Skip the connection test* if the lock is out of range right now.

## Options

*Settings → Devices & services → eufy Smart Lock (BLE) → Configure*

| Option | Default | Effect |
|---|---|---|
| Stay connected | on | Keeps the BLE session open with a 15 s heartbeat so state changes arrive instantly. Reconnects automatically with back-off. Turn off to free the lock for the eufy app between operations; state is then only read at each poll and on each command. |
| Poll interval | 300 s | How often the state and settings are (re)read. |

## Connection behaviour

- **Stay connected** mode holds one BLE link, the same pattern the eufy Wi-Fi bridge uses.
  The lock keeps its own connection parameters, so battery impact is comparable to the
  bridge; motor moves, fingerprint and keypad wakes dominate consumption. A connected lock
  also stops advertising.
- BLE peripherals close the link now and then (the lock drops a session every 5 to 15 min
  in practice). After such a drop the integration reconnects after 1 s and is usually back
  within 5 to 20 s; the session length is logged at info level. The connectivity sensor
  does not react to a drop unless the link stays down for **60 s**, so the logbook is not
  flooded with connected/disconnected pairs.
- When a connection *attempt* fails the loop backs off (5 s doubling to 2 min) and every
  attempt is logged at info level. A lock that went out of range is retried as soon as it is
  seen again. Connect, handshake and disconnect are all bounded by timeouts so a proxy that
  lost the device cannot stall the loop.
- The lock keeps its last known state for **2 minutes** after a drop; if the link is not back
  by then, the entities become unavailable until it reconnects.
- A lock or unlock requested while the link is down waits up to 12 s for the reconnect
  before failing.
- RSSI is only updated from advertisements, so it is blank while connected.

## Troubleshooting

- **"The lock rejected the credentials"**: admin user ID or public key wrong, or Home Assistant's
  clock/time zone is off.
- **Commands fail with result 0x42**: update to 0.2.0 or newer and check Home
  Assistant's clock and configured time zone. The old implementation fixed two
  identity fields at UTC−4. A refusal code by itself does not establish its cause.
- **"Not in range" / no discovery**: the proxy must see the lock as *connectable*. Move the
  proxy closer; enable `sensor.<name>_signal_strength` to check.
- **Cannot connect / frequent drops**: make sure the eufy app is closed and no other
  integration holds a connection to the lock; below about −90 dBm expect drops.
- **"No lock found on this eufy account"**: the account country must be the one the account
  was created with; it selects the eufy server that holds your devices.
- Enable debug logging (very verbose: one line per 15 s heartbeat) with:

```yaml
logger:
  logs:
    custom_components.eufy_lock_ble: debug
```

## Limitations

- Settings are exposed read-only. The lock has a set-parameter command, but its payload has
  not been verified on hardware, so nothing is written to the lock beyond lock/unlock.
- No access log, user or passcode management.
- Only the T8510 has been exercised on hardware.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_dev.txt
ruff check . && pytest
```

`tests/fake_lock.py` implements the lock side of the protocol (ECIES handshake, session
key, commands, state pushes, battery) and `tests/cloud_fixtures.py` mimics the eufy cloud
(including its encrypted list responses), so setup, discovery, lock/unlock, live pushes,
reconnects and the account flow are all tested without hardware. On Windows the Home
Assistant test plugin needs stubs for the POSIX-only `fcntl` and `resource` modules in the
virtual environment.

## Privacy

The repository contains application code and synthetic examples. Each user enters
their own credentials during setup. Passwords and cloud login tokens are not saved
in the config entry. Per-lock credentials stay in the user’s Home Assistant storage.
Identifiers are redacted from integration diagnostics; raw BLE packet bodies and
account inventory dumps are not exported by default. [Details and limits](PRIVACY.md).

## Acknowledgements

This distribution is based on [Leicas/ha-eufy-lock-ble](https://github.com/Leicas/ha-eufy-lock-ble),
by Antoine Weill--Duflos. The original MIT license and copyright are preserved.
The public-maintenance changes add automatic timezone encoding, refusal handling
and privacy improvements. Manufacturer photos remain attributed to eufy/Anker.


- [eufy-security-client](https://github.com/bropat/eufy-security-client) for the cloud API
  and the ECIES/KDF construction.

## Disclaimer

Not affiliated with Anker/eufy. Use at your own risk: this software operates a physical
door lock.
