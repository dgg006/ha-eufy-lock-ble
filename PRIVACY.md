# Privacy and public repository boundaries

This repository distributes integration code. It does not include a maintainer's
Home Assistant configuration, account export, captured traffic or device credentials.
Examples and tests use synthetic identifiers and locally generated test keys.

## User data at setup and runtime

- Optional cloud setup sends the user's eufy account login to eufy's servers to
  obtain the selected lock's credentials. The password and temporary cloud token
  are not written into the integration's config entry.
- The config entry stores the Bluetooth address, serial, device name, admin user
  ID, static lock public key, short user ID and nickname required for operation.
  These belong in each user's Home Assistant storage, never in this repository.
- Runtime commands travel through local Bluetooth, directly or through the user's
  Home Assistant Bluetooth proxy. Setup country selects the eufy account server;
  the identity timezone comes from Home Assistant's configured timezone.
- This integration adds no analytics endpoint or reporting service.
- Access records contain household names, user IDs, methods and timestamps. The
  latest 100 records and their original bytes remain in HA's local history cache;
  removing the integration entry deletes that cache. Enabled history sensors and
  activity entities expose these data within HA and may be recorded there.

## Diagnostics and logging

Integration diagnostics redact address, serial, name, account IDs, nickname,
credentials and nested token/password fields. They retain operational information
such as firmware, connectivity, battery, times and recognized settings for support.
Unknown parameter bytes are omitted from the diagnostics export.
Access-history records, names, user IDs, event times and raw bytes are also omitted;
only history read status, baseline completion and record count are included.

Integration debug logs record command IDs and lengths instead of raw BLE payloads.
The credential helper prints only credentials for the explicitly selected lock,
not a complete account inventory. Its output is private and is not a bug report.

Home Assistant itself, Bluetooth libraries and other integrations may log device
identifiers. Redaction here does not guarantee that an entire HA log or backup is
anonymous. Review any file before posting it publicly.

## Public releases

The public source and HACS archive contain only the reviewed repository files.
Local access tokens, `.storage`, account dumps, reports from a real installation,
APK/DEX/native app binaries and packet captures are excluded. The release workflow
packages only `custom_components/eufy_lock_ble/`.

The original MIT copyright is preserved. The manufacturer product image is linked
and attributed separately; it is not a photograph of a user's installation.
