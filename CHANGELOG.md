# Changelog

Versions match `grok_voice_agent/config.yaml`. GitHub Releases list the
merged pull requests since the previous tag. Issues closed with `Fixes #N`
in those pull requests are included below when GitHub reports them.

## [0.2.2] - 2026-09-29

## What's Changed
* Add Grok Voice Web UI, store install, and release workflows by @fabianluque in https://github.com/fabianluque/ha-grok-voice/pull/1
* Fix Open Web UI ingress not-ready dialog by @fabianluque in https://github.com/fabianluque/ha-grok-voice/pull/2
* Add a one-step Release workflow for GHCR and GitHub Releases by @fabianluque in https://github.com/fabianluque/ha-grok-voice/pull/3
* Give the Release workflow a token for gh by @fabianluque in https://github.com/fabianluque/ha-grok-voice/pull/4
* Use the signed-in Open Web UI session instead of a pasted token by @fabianluque in https://github.com/fabianluque/ha-grok-voice/pull/5

## New Contributors
* @fabianluque made their first contribution in https://github.com/fabianluque/ha-grok-voice/pull/1

**Full Changelog**: https://github.com/fabianluque/ha-grok-voice/commits/v0.2.2

## [0.2.1] - 2026-09-29

- Open Web UI serves the microphone page instead of failing the ingress request as not ready.

## [0.2.0] - 2026-09-29

- Browser microphone page served by the add-on on Open Web UI and port 8080.
- Home Assistant add-on store layout (`repository.yaml` and `grok_voice_agent/`).
