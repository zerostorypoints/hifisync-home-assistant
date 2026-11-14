# HiFiSync for Home Assistant

This standalone, read-only custom integration connects one Home Assistant installation, one registered media player and one explicitly shared HiFiSync system. It refreshes system and room presentation data locally; it does not require Frame or Studio. This pilot is not in the HACS default catalog or Home Assistant core.

## Prerequisites and installation

Use Home Assistant 2026.9.2 or newer and a registered `media_player` entity. Create a HiFiSync installation token in your account, explicitly granting the systems you want to share. The token is shown once. Your Home Assistant administrator needs to paste it during setup. Keep the token private.

Once this repository has a published release, add its repository URL to HACS as a custom **Integration**, install HiFiSync, and restart Home Assistant. Until a release is published, copy this repository's `custom_components/hifisync` directory to `<config>/custom_components/hifisync` and restart. In **Settings → Devices & services → Add integration**, choose **HiFiSync**, enter the token, select an allowed system and a registered player. Pick the actual streaming integration when duplicate Cast or DLNA entries exist. A token whose scope is empty displays an explanation; update the grant and start setup again.

The token is stored in the Home Assistant config entry. Its snapshot stays in memory and is exposed only through a local, player-scoped integration interface. The status sensor reports link readiness without room details. The integration sends no player identifiers, playback information, commands or changes to HiFiSync.

## Operations

Use the integration's **Configure** action to select a different system or player. The player's registry identity survives entity renames; if the entry is removed or replaced, configure the binding again. Deleting or unsharing the selected system clears the snapshot on the next successful validation and retains the token and player for reselection. Revoking or expiring the token clears the snapshot and starts Home Assistant reauthentication; paste a replacement token. A previous selection is restored only after the replacement token validates it. If it no longer has access, select a system again.

Refresh runs every five minutes, on playback when due, and retries temporary errors with bounded backoff. A last validated snapshot is marked stale on a temporary error and expires no later than one hour after validation or the token expiry, whichever comes first. Removing or unloading the integration drops the in-memory snapshot. Generic sensor state and recorder do not contain the system or room projection. Frame or another local consumer must independently enforce user permissions and local projection expiry.

## Development and release

Tests use fictional data and an isolated HA test instance. With Python 3.14 and [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.14
uv pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
```

For a future release, verify on an independent HA installation, review the version in `manifest.json` and minimum version in `hacs.json`, then publish a tagged release through the repository's authorized release process. This pilot creates no remote or release. To update, install the reviewed HACS release (or replace the custom component directory), then restart HA. To roll back, restore the previously installed component directory or version and restart; existing configuration remains in HA. Remove the integration in HA if you want to disconnect, and revoke its token in HiFiSync separately.
