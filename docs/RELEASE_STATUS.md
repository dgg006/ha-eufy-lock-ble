# Release validation status

Version: **0.3.2**. Prepared on 2026-10-19.
Public repository: `dgg006/ha-eufy-lock-ble`.

## Completed locally

- 121 tests passed, including timezone vectors, DST, encrypted sessions, cloud/setup
  flows, entities, rejection handling, diagnostics redaction and model identification.
  History checks cover encrypted queries, nonempty final pages, overlap, background
  pagination, stalled scans, delayed records, startup replay suppression and privacy.
- Alias tests cover selecting observed users, editing/removing local names, keeping
  aliases when connection settings change, stable record IDs, diagnostic redaction
  and immediate sensor updates without reconnecting Bluetooth.
- Capture tests cover annotation-only buttons, polling pause, duplicate reports,
  bounded duration/report count, atomic files, checkpoints, shutdown and write failures.
- Ruff lint passed. Git whitespace validation passed.
- Public file-boundary scan passed. The reviewed source tree contains no private
  access file, real installation export, account dump, APK/DEX/native app binary,
  packet capture or investigation scripts.
- Source and integration-only ZIP archives were generated from reviewed files.
- T8510 firmware 2.0.2.3 physical lock/unlock and readings were confirmed using the
  timezone correction. The history candidate was installed on Home Assistant 2026.10.1
  and completed a real Bluetooth history scan. The returned records include two
  distinct fingerprint-user IDs, passcode, app, manual and fingerprint-lock events.
- New physical trials of both fingerprints, PIN and key are pending user availability.
  A history scan proves those records can be decoded; it does not measure new-event
  latency or distinguish mechanical key use from the interior thumb-turn.

The local test run used Windows and Home Assistant 2026.2.3. Unused POSIX process
helpers (`fcntl`, `resource`) were supplied by a local harness outside this repository.
The optional D-Bus extension ran as pure Python. The Windows LRU wheel was 1.4.1
because the pinned 1.3.0 release has no compatible Windows wheel. The Linux CI
workflow retains the original pinned test requirements. GitHub results are tracked below.

## GitHub validation

The public repository was created on 2026-10-14 with installation buttons for HACS
in both READMEs. GitHub-hosted tests, Hassfest and HACS validation run on each push;
their results are available in the repository's Actions tab. Release publication
follows successful checks.
The 0.2.0 Linux run passed 98 tests, HACS and Hassfest. Consult the Actions run for
the 0.3.0 commit for that version's separate results.

Default HACS catalog inclusion is a separate optional submission and has not been
requested. Existing MIT copyright and upstream credits remain.
