# Local Bluetooth advertisement capture

Version 0.3.2 adds a diagnostic that runs **on Home Assistant**, using its existing
Bluetooth adapters/proxies. The computer used to develop or inspect the integration
does not have to be at the lock's location or remain running.

## Run the test

1. In the integration options, disable **Keep connected**. Close the eufy app.
2. On the lock's device page, press **Start Bluetooth capture**.
3. Leave the lock untouched for about one minute to record a baseline.
4. Immediately before each test, press its **Mark ... test** button, then operate
   the lock physically. Available annotations: fingerprint, passcode, physical key
   and interior thumb-turn. Mark each separate action and wait about 15 seconds
   between actions. If testing both locking and unlocking, mark both operations.
5. Press **Stop Bluetooth capture**, or let the ten-minute timer finish.
6. The **Bluetooth capture** sensor shows the status, report count, distinct
   payload count, markers and output filename. Counts refresh at checkpoints
   (about every 30 seconds) and when a marker is added.

Annotation buttons **never operate the bolt**. Lock/unlock commands from HA remain
available, but they add an `ha_command` marker and connect to the lock, so avoid
them during a physical-interaction experiment. Other apps/bridges can also connect;
the integration cannot prevent their traffic.

Automatic state/history polling is paused only while the capture is active. It
resumes on its normal schedule afterwards; options are not changed. Advertisements
are observed without initiating a GATT connection or changing the proxy's scanning
settings. A ten-minute ceiling and 20,000-report ceiling bound each capture.

## File and privacy

Files are saved inside the HA configuration directory, under:

```
eufy_lock_ble_captures/capture_<UTC timestamp>_<random suffix>.json
```

For example, File Editor normally shows the directory under `/homeassistant/`,
while other environments call the same configuration directory `/config/`.
The sensor exposes the filename; the directory can be opened in File Editor to
download it. It is deliberately outside `www`, so it has no unauthenticated URL.

The log contains Bluetooth identifiers and the target lock's advertising bytes.
Keep it private. It does not include stored HA/eufy login credentials, user aliases
or the access-history cache. Only the selected lock's address is observed.
Capture files are ignored and rejected by the public repository packaging checks.

A checkpoint is written every 30 seconds, with a final save when stopped or
unloaded. A reload/restart ends the capture rather than silently resuming it.
Previously saved files remain in the configuration directory. An abrupt process
or power failure can lose reports since the most recent checkpoint.

## What this can establish

On HA versions exposing `async_register_advertisement_callback`, mode is
`every_delivered_advertisement`, including unchanged reports and reports from
different scanners. Older versions fall back to `changed_advertisements_only`.
The fallback mode cannot establish whether identical packets were repeated.

Each report includes UTC receipt time, elapsed monotonic time, scanner source,
RSSI, transmit power if available, manufacturer data, service data/UUIDs, payload
identity and raw advertisement bytes **when supplied by the backend**. A cached
pre-capture report is flagged using its original timestamp where available.

HA may merge manufacturer/service fields across packets. A proxy/scanner can
filter duplicates or miss packets before HA receives them. Therefore this is a
capture of reports delivered to HA, **not a complete radio sniffer**. A negative
result does not prove that the physical lock never transmits an event signal.

Compare the baseline with the marked operations to look for changes in bytes,
counters, flags, advertisement presence or cadence. The collector does not yet
interpret a flag as a reliable door event or trigger a connection automatically.

Reference: [Home Assistant Bluetooth APIs](https://contributors.home-assistant.io/core/integrations/bluetooth/).
