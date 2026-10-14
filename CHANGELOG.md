# Changelog

## 0.2.0

- Derive both VERIFY timezone fields from Home Assistant local time at every handshake.
  This removes the fixed UTC−4 assumption and supports timezone/DST changes.
- Confirmed T8510 firmware 2.0.2.3 lock/unlock, status, battery and settings reads.
- Treat rejected status/settings queries as errors before publishing callbacks.
- Redact account and device identifiers in integration diagnostics, including nested tokens.
- Omit raw BLE bodies from integration debug logs and unknown bytes from diagnostics.
- Replace full-account credential-helper dumps with selected-lock output only.
- Add model identification, manufacturer image/source links, Spanish documentation,
  timezone regression tests and publication checks.
- Add a direct HACS installation button in the English and Spanish READMEs.

Based on Leicas/ha-eufy-lock-ble 0.1.0. Original MIT license retained.
