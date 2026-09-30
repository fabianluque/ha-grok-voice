# Changelog

Versions match `grok_voice_agent/config.yaml`. GitHub Releases list the
merged pull requests since the previous tag. Issues closed with `Fixes #N`
in those pull requests are included below when GitHub reports them.

## [0.2.14] - 2026-09-30

- Keep **one in-place You: line** when Grok ASR revises the same utterance. 0.2.13 still appended another bubble when a later `updated` snapshot changed wording (not a prefix of the previous text), including after `speech_stopped` / `completed`. Overlay now keys the live bubble by ASR `item_id` and only starts a new You: line at a true turn boundary (new item, or `speech_started` after `speech_stopped`).

## [0.2.13] - 2026-09-30

- Fix kiosk overlay user-transcript streaming. Each xAI `conversation.item.input_audio_transcription.updated` snapshot now replaces the single live You: line instead of appending another bubble that repeats the growing prompt. Extra `speech_started` events mid-utterance no longer finalize that bubble; hang-up still waits for a completed (`final: true`) closer.
- If the dashboard or inject reloads mid-conversation, hang up the orphan duplex and hand the microphone back to Kiosk Satellite so “hey grok” is not left dead (`pagehide` / `beforeunload`, plus re-arm on inject boot).
- Tap the full-screen conversation overlay to dismiss (same hang-up path as goodbye/`end_session`: drain ack TTS if Grok is speaking, then close the duplex and re-arm KS). Scrolls are ignored; a tap anywhere on the overlay hangs up.

## [0.2.12] - 2026-09-30

- On a new wake/resume, the overlay shows **this session only**. Reinjected ~8 minute per-device history still goes to Grok (`session.update` instructions plus `conversation.item.create`) so follow-ups work, but those echoes are not painted on screen.

## [0.2.11] - 2026-09-30

- Let Grok’s hang-up ack finish playing before the duplex closes. `end_session` (command or dismiss) and closing-phrase hang-up wait until queued/playing assistant audio drains (the same sequential playback path used for tool TTS), then close the socket and microphone and re-arm Kiosk Satellite wake. Mid-session tool replies still are not clipped.
- Shrink kiosk overlay conversation type by about 30% (`clamp(18px, 2.9vw, 28px)`). Streaming user/Grok lines update the in-progress bubble in place as each transcript delta arrives, without rebuilding the whole log.
- Stream **user** speech into the overlay as it is recognized. xAI does not emit OpenAI `input_audio_transcription.delta`; it emits cumulative `conversation.item.input_audio_transcription.updated` snapshots when `audio.input.transcription.model` is `grok-transcribe`. The overlay replaces the live You: line with each snapshot (including ASR revisions).

## [0.2.10] - 2026-09-30

- Name the city in session location context. Add-on **Home location** (`Summit, NJ`) is expanded to **Summit, New Jersey** in the Grok instructions so answers can say the city, not only a vague home/GPS string.
- Keep short per-device conversation memory across wakes within the TTL. History is keyed by a stable tablet id (not area fallback), written as turns complete (including xAI `updated` transcripts after VAD stop), and reinjected on the next `session.update` both as instructions and as `conversation.item.create` messages so follow-ups like “do I have a meeting today?” use what the user just said, not only the calendar tool. Attic vs dining stay separate. Goodbye still clears that device; idle hang-up keeps the turns (~8 minutes).
- Increase local mic pre-roll to **900ms** and flush it when the duplex session is `ready` (not at WebSocket open). Server VAD `prefix_padding_ms` is **800ms** and threshold **0.35**. getUserMedia is still not delayed.
- Hang up after a one-shot home command or a dismissal without an exact goodbye phrase. Grok gets a local **`end_session`** tool (`reason=command` or `reason=dismiss`). After a short ack, the duplex ends and wake-word listening resumes (same path as goodbye). Phrase matching also accepts “you can go now” / “thanks I’m done”. Do not auto-end mid-multi-step or while asking a question. Command hang-up keeps the 8-minute tablet history; dismiss/goodbye still clears it.
- Kiosk overlay is full-screen (blur + dim over the dashboard, Listening/Speaking on top) with larger conversation type (`clamp(26px, 4.2vw, 40px)`) so Fire tablets can read it across a room. User and Grok text update as transcript/response deltas arrive, not only on the final chunk. Each tablet still has its own overlay and session.

## [0.2.9] - 2026-09-30

- Inject the current local date/time and home location into every Grok session at open (server-side `session.update` instructions). Timezone and GPS come from Home Assistant `/api/config` plus `zone.home`; the add-on **Home location** option supplies a city/ZIP such as Summit, NJ. The clock is computed when the session starts, not baked into the add-on.
- Keep a short per-device conversation memory after hang-up so a new wake on the same satellite can follow up. Keyed by kiosk device (then area); TTL defaults to 8 minutes (`conversation_memory_ttl_seconds`). Cleared when that timer expires or the user says goodbye.
- Soften server VAD slightly and include 400ms of audio pre-roll (`prefix_padding_ms`) so the first syllable after snappy listen-start is less likely to be clipped. Area lookup stays off the wake path.

## [0.2.8] - 2026-09-30

- Serve the kiosk IIFE from the add-on at a stable URL (`http://<HA-LAN>:8080/grok-voice.js`, same port as duplex). The attic Kiosk Satellite inject is a one-time bootstrap; updating the add-on refreshes the client without pasting the full script into Remote Admin. Dining-room dashboards that never load the bootstrap stay untouched.

## [0.2.7] - 2026-09-30

- Restore snappy listen-start on the attic kiosk inject. 0.2.6 awaited Home Assistant device/entity/area registry lists (and `getDeviceInfo`) on every wake before `getUserMedia` and the duplex socket. The inject now prefetches the area at boot and sends the cached/explicit/Attic fallback on auth immediately. Registry lookups run in parallel in the background. The duplex WebSocket opens while the mic is claimed.
- Attach Assist media, todo, and Mealie tools on a blank allowlist (`HassMediaSearchAndPlay`, pause/volume/next, `HassListAddItem`, `HassGetState`, plus `music_assistant` / `media_player` / `todo` / `mealie` domains). Bare "play music" / "play X" from the attic tablet targets Music Assistant in the session area (Attic HomePod Mini) and does not ask which speaker.

## [0.2.6] - 2026-09-30

- Hang up when a completed utterance *ends* with a closer (`oh, that's great, thank you`), not only when the whole phrase is exactly `thank you` / `goodbye`. A closer in the middle of a request still does not end the session.
- Do not replace mid-reply speech when a tool runs or a second TTS generation starts. New audio waits until the current sentence finishes unless the user barges in.
- Show a conversation overlay on the attic kiosk during a duplex session (Listening / Speaking plus user and Grok transcripts). It hides when the session ends and wake is re-armed.
- Scope lights, music, and other room commands to this kiosk's Home Assistant area. The kiosk sends the tablet's HA/KS area; the add-on also has `default_area` (Attic) and optional `default_area_id`. Bare "turn on the lights" uses that area on Assist tools and does not ask which room. Override with `window.GROK_VOICE_AREA` / `GROK_VOICE_AREA_ID` in the inject if needed.
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
