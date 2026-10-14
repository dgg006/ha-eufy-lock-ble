# Release validation status

Version: **0.2.0**. Prepared on 2026-10-14.
Public repository: `dgg006/ha-eufy-lock-ble`.

## Completed locally

- 98 tests passed, including timezone vectors, DST, encrypted sessions, cloud/setup
  flows, entities, rejection handling, diagnostics redaction and model identification.
- Ruff lint passed. Git whitespace validation passed.
- Public file-boundary scan passed. The reviewed source tree contains no private
  access file, real installation export, account dump, APK/DEX/native app binary,
  packet capture or investigation scripts.
- Source and integration-only ZIP archives were generated from reviewed files.
- T8510 firmware 2.0.2.3 physical lock/unlock and readings were confirmed using the
  timezone correction. The additional publication/privacy changes were tested locally;
  they have not been installed into that running Home Assistant instance.

The local test run used Windows and Home Assistant 2026.2.3. Unused POSIX process
helpers (`fcntl`, `resource`) were supplied by a local harness outside this repository.
The optional D-Bus extension ran as pure Python. The Windows LRU wheel was 1.4.1
because the pinned 1.3.0 release has no compatible Windows wheel. The Linux CI
workflow retains the original pinned test requirements and needs to run on GitHub.

## GitHub validation

The public repository was created on 2026-10-14 with installation buttons for HACS
in both READMEs. GitHub-hosted tests, Hassfest and HACS validation run on each push;
their results are available in the repository's Actions tab. Release publication
follows successful checks.

Default HACS catalog inclusion is a separate optional submission and has not been
requested. Existing MIT copyright and upstream credits remain.
