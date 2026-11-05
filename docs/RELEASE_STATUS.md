# Release validation status

Version: **0.3.5**. Prepared on 2026-11-05.
Public repository: `dgg006/ha-eufy-lock-ble`.

0.3.5 updates attribution, distributed licenses and version metadata only. Runtime
code is unchanged from 0.3.4. Both MIT copyright notices ship inside the integration.

New installations use on-demand connections and 900-second polling while premature
AA battery depletion remains under investigation. This precaution is not a measured
battery fix. Physical operations may only appear at the next successful poll.
Existing entries migrate with their saved or previous implicit settings intact;
credentials, aliases, entity identifiers and history are retained.

## Local verification

- 131 tests passed: encrypted sessions, controls, status, timezone/DST, cloud login,
  timeout and malformed JSON recovery, HTTP login rejection, task cancellation,
  history pagination/deduplication, aliases, privacy and bounded capture.
- Migration tests preserve both implicit legacy defaults and explicit settings and
  aliases. A new entry test confirms on-demand operation and 900-second polling.
- Ruff and the public-tree privacy scan passed. Candidate source and integration
  archives were checked for known private installation identifiers without finding
  any matches. Private app binaries and research captures are not distributed.
- The local run uses a Windows harness outside the repository for unused POSIX
  helpers. GitHub CI runs on Linux with socket blocking enabled; its results are
  linked from the release and the HACS submission.

## Physical validation and limits

Earlier hardware checks verified T8510 firmware 2.0.2.3 lock/unlock, status and
history reads. This release was tested with synthetic devices and cloud responses;
it has not been installed on the user's Home Assistant as part of publication.
No new physical operation, battery endurance or event-latency test is claimed.
Other models remain unverified.

## Publication

Publish only after the commit's Linux tests, HACS and Hassfest pass, then verify the
release tag as well. The stable release matches manifest.json and pyproject.toml.
The installation buttons work as a HACS custom repository while the catalog PR is
under review; catalog acceptance is not claimed. Once accepted, lead the README
installation instructions with searching for the integration in HACS.

The original MIT license and upstream icon artwork remain; provenance is documented
in [BRAND.md](BRAND.md).
