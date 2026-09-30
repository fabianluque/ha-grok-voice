# Changelog

Versions match `grok_voice_agent/config.yaml`. GitHub Releases list the
merged pull requests since the previous tag. Issues closed with `Fixes #N`
in those pull requests are included below when GitHub reports them.

## [0.2.6] - 2026-09-30

- Hang up when a completed utterance *ends* with a closer (`oh, that's great, thank you`), not only when the whole phrase is exactly `thank you` / `goodbye`. A closer in the middle of a request still does not end the session.
- Do not replace mid-reply speech when a tool runs or a second TTS generation starts. New audio waits until the current sentence finishes unless the user barges in.
- Show a conversation overlay on the attic kiosk during a duplex session (Listening / Speaking plus user and Grok transcripts). It hides when the session ends and wake is re-armed.
- Scope lights, music, and other room commands to this kiosk's Home Assistant area (from KS `getDeviceInfo` or the HA device/assist area). Fall back to Attic only when that area is not readable yet. Optional inject override: `GROK_VOICE_AREA` / `GROK_VOICE_AREA_ID`.
- Call `setTimeout` / `clearTimeout` as methods of `globalThis` so Chromium WebView no longer throws `Illegal invocation` about every 10s during a live session.

## [0.2.5] - 2026-09-30

- Hang up the voice session on goodbye phrases or after `idle_timeout_seconds` of silence once Grok has finished speaking. The idle timer uses xAI server VAD (`speech_started` / `speech_stopped` / `response.done`) instead of raw microphone PCM, so a live duplex session no longer keeps the mic open forever. xAI `turn_detection.idle_timeout_ms` is not set; that option only starts a proactive check-in.

## [0.2.4] - 2026-09-29

- Attach Home Assistant tools to the Grok voice session. Home Assistant 2026.9 lists them as `intent__HassTurnOn`, `intent__HassTurnOff`, `light__HassLightSet`, and `homeassistant__GetLiveContext`. A blank allowlist matches those prefixed names as well as the older bare names. When a voice session starts, the log reports how many tools were listed and attached.

## [0.2.3] - 2026-09-29

- Home Assistant tools use the Supervisor add-on token. The service is started with `with-contenv`, so `SUPERVISOR_TOKEN` is visible and an empty `Bearer` header is not sent. Leave the MCP URL and long-lived token blank. The startup log should say `mcp_auth=supervisor`.

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
