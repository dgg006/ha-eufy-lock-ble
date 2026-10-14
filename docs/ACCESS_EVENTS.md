# Access history and events

Version 0.3.0 adds the Anker Bluetooth history query (`0x18`). This is a read-only
request: the integration never deletes, acknowledges away or clears lock records.
The immediate `0x12` notification still supplies bolt state and battery; identity
and access method come from querying history, not from that short notification.

## Entities

- **Activity** (`event`): each newly observed record after the initial history scan.
- **Last event** and **Last event time**: latest access, lock operation or alarm.
- **Last unlock method**, **Last unlock user** and **Last unlock time**: latest
  unlock, retained even after a subsequent lock or alarm.
- **History status**: `pending`, `reading`, `ready`, `error`, `stalled`, `truncated`
  or `refused_XX`. Includes baseline completion and cached record count.
- **Access history**: last 100 observed records, disabled by default.
- **Raw access history**: same records plus the original 40-byte hex record,
  disabled by default. This preserves unknown fields for local investigation.

Each decoded record includes the original numeric event code, stable event type,
timestamp, UTC date/time, action, method, reported short user ID and user name.
Unknown event codes remain available as `unknown` with their numeric code intact.
The original bytes remain in the local cache even if their event type is unknown.
Malformed/truncated pages fail visibly instead of inventing user or method data.

The app's shared event vocabulary includes passcode, fingerprint, app, manual,
automatic, one-touch, temporary passcode, NFC, low battery, mechanical problems,
repeated errors and tamper events. Other model families add face, palm and Matter
codes. Decoding a code does **not** establish that the T8510 supports that feature.
Only events actually returned by the connected lock appear as activity.

## Identity limitations

The original name and ID are the values reported by the lock, not an inferred HA person.
Starting in 0.3.1, local display aliases can be edited in the integration's Configure
form: enable **Edit user display names**, select a user and enter a display name.
Clear the field to remove an alias. Each alias belongs to a stable lock-user ID,
not to a masked name. It remains configurable even if that user falls out of the
100-record cache. The user must have been observed at least once to first select it.

`user_name` is the local display name (alias if configured); `reported_user_name`
retains the original lock name. `user_id`, raw records and deduplication IDs never
change. Sensors and history update immediately; future events use the new name.
Previously emitted events are not replayed or rewritten when a name is edited.
Automations identifying a person should filter the stable `user_id`, since display
names can change. Names are not sent back to eufy or written into the lock.

Different fingerprints can identify different users only if the lock associates
them with different user records. Multiple fingerprints belonging to one user
may not be distinguishable. Empty fields stay empty; an ID is shown as a fallback
if the last unlock has no name. For manual/automatic/ambiguous one-key operations,
the last-unlock-user sensor stays unknown; the original reported user fields remain
available in attributes/history without attributing the action to that person.

`manual` must not be labelled as a particular person or as a confirmed physical
key: the lock may group key and interior thumb-turn operations. The protocol
records do not carry a fingerprint image, fingerprint template or plaintext PIN.

## Timing, pagination and automation

Persistent mode reads history on connection, approximately every 60 seconds,
and shortly after a bolt-state change. Large histories are read in chunks of four
pages, yielding between chunks so controls and heartbeats can run. On-demand mode reads during its
configured polling interval; it does not open a second connection just for events.
Actual latency depends on the lock's record creation and Bluetooth availability.

The first complete scan on every integration start is a quiet baseline: sensors
populate but existing records do not fire activity or bus events. Subsequent new
records fire once, including records recovered after a temporary disconnection.
Records older than two minutes are marked `historical: true`; future timestamps
are retained for inspection but do not fire events. Record timestamp resolution is
one second, and byte-identical same-user operations in the same second cannot be
reliably distinguished. Overlapping pages are deduplicated.

The event entity's state is **receipt time**; `occurred_at` is the lock's reported
event time. For automations, listen for `eufy_lock_ble_event`. The payload includes
`config_entry_id`, `record_id`, `timestamp`, `occurred_at`, `received_at`,
`event_code`, `event_type`, `action`, `method`, `user_id`, `user_name`,
`reported_user_name` and `historical`.
For actions that should happen only on a recent opening, filter explicitly:

```yaml
triggers:
  - trigger: event
    event_type: eufy_lock_ble_event
    event_data:
      event_type: fingerprint_unlock
      historical: false
```

For multiple locks, also filter `config_entry_id` from your own HA instance.
The integration itself takes no action in response to access records.

Pagination follows the official app's cumulative record count minus one. Empty,
repeated or non-advancing pages stop a scan as `stalled`; exceeding the protocol's
16-bit record-index range is reported as `truncated`. Unfinished/error scans never masquerade as
successful empty histories. History errors do not disable lock/unlock or battery.

## Local storage and privacy

Recent records, including original bytes, are stored in HA's local `.storage`
under an integration-specific history key. They are restored for display on a
restart and removed when the integration entry is deleted. The latest 100 records
are retained; this is not a permanent archive of every lock operation. The normal
HA recorder may separately retain enabled sensors and event entities.

History names, user IDs, timestamps and raw frames are **not** included in exported
integration diagnostics or integration debug logs. Diagnostics contain only read
status, baseline completion and record count. Do not publish `.storage` files,
raw-history attributes or screenshots containing household activity.
