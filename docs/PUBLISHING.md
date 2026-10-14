# Publishing and HACS

Public repository: [dgg006/ha-eufy-lock-ble](https://github.com/dgg006/ha-eufy-lock-ble).
The README installation buttons open this custom repository in HACS.
No default catalog acceptance is implied.

## Before each public release

1. Review the diff against the MIT-licensed upstream project. Keep its license and credits.
2. Confirm the repository owner/name matches `manifest.json`, README links and issue links.
3. Run the tests, lint, public-tree scan, Hassfest and HACS validation. GitHub-hosted
   checks require the actual repository and cannot be confirmed by a local file inspection.
4. Review the exact source archive and its file list. Do not upload the investigation
   directory, account credentials, original app binaries or a Home Assistant backup.
5. Publish a GitHub release matching the integration manifest version (currently `v0.3.1`).

The release workflow creates `eufy_lock_ble.zip`, with integration files at its
root. HACS currently installs from the repository's standard `custom_components`
layout; the ZIP is also suitable for manual installation. `zip_release` is not enabled.

## HACS installation versus default catalog

The first distribution method is a **custom repository**, category Integration.
Users add the repository URL to HACS themselves. The repository must be public.

Inclusion in the default catalog is a later submission to `hacs/default`. The HACS
and Hassfest actions must pass, a GitHub release must exist, and the repository's
description, topics, model documentation and brand assets must meet the current rules.
The existing integration `brand/` assets are retained; CI checks their acceptability.

Suggested GitHub topics: `home-assistant`, `hacs`, `hacs-integration`, `eufy`,
`bluetooth-le`, `esphome-bluetooth-proxy`, `smart-lock`.

## Migration

The fork retains the `eufy_lock_ble` domain and entity identifiers. Back up HA using
the user's normal backup process, remove the upstream repository from HACS, add this
repository and install into the same integration directory. Restart HA; do not
create another config entry just to change repositories. Do not leave duplicate
integration folders under `custom_components`.

## Sources

- [HACS integration requirements](https://www.hacs.dev/docs/publish/integration/)
- [HACS general requirements](https://www.hacs.dev/docs/publish/start/)
- [Default catalog submission](https://www.hacs.dev/docs/publish/include/)

Reviewed on 2026-10-14. Recheck the requirements at publication time.
