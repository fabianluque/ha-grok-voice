# Changelog

Versions match `grok_voice_agent/config.yaml`. GitHub Releases list the
merged pull requests since the previous tag. Issues closed with `Fixes #N`
in those pull requests are included below when GitHub reports them.

## [0.3.9] - 2026-11-13

- No goodbye after home commands. Kiosk Satellite's `end_conversation` description told Grok to "say a short goodbye in the same response", and the relay passed it through. The relay now replaces that description (name and parameters unchanged) with "End the voice conversation. Call it when the user is done or after a home command. Do not say goodbye after a home command; after thanks/goodbye a brief reply is fine." The hang-up follow-up instruction now reads "Action succeeded. Say only a short confirmation (no goodbye or sign-off), then call end_conversation. Do not repeat anything." The default extra instructions say the same.
- Better per-tablet memory. If the same tablet's last session ended within the new **replay minutes** setting (`memory.replay_minutes`, default 10, 0 = off; on the panel), its last user / assistant exchanges (up to 6) are replayed as `conversation.item.create` messages right after the session starts, the way the old duplex client did. They never trigger a response, their echoes are not forwarded to the tablet, and hang-up, transcript filtering and the audio allowance are unaffected. Older items stay in the summary block (window still 45 minutes); the replayed session appears there as its tool facts only.
- Tool facts keep their targets, e.g. `turn_on ok (TV room lights)`: the entity names Home Assistant matched, else the name / area asked for. No other arguments are stored.
- The recent-context block now tells Grok: "If the user says them/it/that without naming a device, it refers to the most recent device below." Each session logs and marks on the timeline (`memory_injected`) how many summary and replay items it got.

## [0.3.8] - 2026-11-13

Fixes tablets hearing only a blip of each reply after 0.3.7.

- xAI streams a reply's whole transcript within about 0.1 s of its first audio, seconds ahead of most of the audio, and the streamed text often ends with a harmless `<turn_end>`. 0.3.7 treated any tag as a leak, so normal replies were cut and cancelled right after the first audio and every later audio delta was dropped. End-of-turn markers (`<turn_end>`, `</turn_end>`, `<end_of_turn>`) and other tags are now stripped silently. Only a `<system…>` block (such as `<system_warning>`) or a sentence repeated word for word cuts a reply.
- A cut never cancels or drops audio straight away. It stops the reply's text and gives its audio an allowance sized to the clean text that was forwarded: 75 ms per character plus 0.4 s, measured from the audio bytes using the session's format (PCM16 or G.711) and sample rate. Audio deltas keep reaching the tablet until the allowance is used up. Then the relay sends `response.cancel`, drops the rest, and sends the optional flush (`flush_tablet_audio_on_cut`). A reply that finishes inside its allowance is left alone. Hang-up after home commands, the relay's `end_conversation` and dropping output after an end work as before. The timeline shows the allowance on `transcript_cut` and the audio played on a new `audio_cut` event.

## [0.3.7] - 2026-11-13

Kiosk Satellite relay fixes for double confirmations, double goodbyes and leaked internal text.

- Home commands that match nothing in the tablet's own room retry once without that room (on/off, lights, covers, locks, climate, fans, to-do lists). Grok was given the tablet's room as the default, so "open the left garage" from the kitchen arrived as `area: Kitchen`, failed, and Grok retried and confirmed twice. Only the tablet's own room is dropped (an area the user named stays strict), and a device or list name must remain so a retry never widens to the whole house. The result carries `area_dropped`.
- Assistant transcripts sent to the tablet are cleaned of `<turn_end>`, `<system_warning>…</system_warning>` (with its content) and any other `<…>` tag, in both delta and done events. A reply that leaks a tag or repeats a sentence word for word is cancelled at xAI (`response.cancel`), the tablet gets the clean text up to that point, and the rest of that reply's audio and text is not forwarded. New setting **Flush tablet audio when a reply is cut** (default off) also sends the tablet a synthetic `speech_started` so it drops buffered audio.
- Hang-up after home commands (new setting `hangup_after_home_command`, default on, per-tablet override). When every relay tool call in an answer is a successful home-control write, the follow-up `response.create` carries "Action succeeded. Confirm once in one short sentence, then call end_conversation. Do not repeat anything." If that answer finishes without `end_conversation`, the relay sends the tablet its own `end_conversation` call (`call_id` `relay_end_…`) just before the held `response.done` and drops the tablet's `function_call_output` for it. Q&A stays open, and an answer that asks a follow-up question is not hung up. "Thanks" / "that's all" from the user ends the same way after the reply (`hangup_on_thanks`, default on). The default extra instructions gain a policy line (hang up after home commands, keep Q&A open, confirm once).
- After any `end_conversation` (Grok's or the relay's), responses xAI starts before the session closes are cancelled and never reach the tablet, and tool calls in them are not run.
- xAI `web_search` / `x_search` calls no longer count as tablet calls: an answer that called a relay tool and a search used to skip the spoken follow-up and go silent.
- The relay's pending-follow-up flag clears on `response.created` / `response.done` or after 10 s, so it can no longer swallow a later real error.
- Every event the relay sends xAI has an `event_id` starting with `relay_`. Errors for those, plus the harmless races "already has an active response" and item / call id not found, are recorded on the session timeline (marked not sent) instead of reaching the tablet. A race on the tablet's own event (its turn's `response.create`) is still forwarded, because Kiosk Satellite handles it.

## [0.3.6] - 2026-10-15

- Skills imported from Home Assistant `script.skill` (`ha_skill` actions with an empty args spec) pass every argument through instead of rejecting them as unknown. Declared args on `ha_skill` still validate. Import may fill simple `{arg}` lists from HA help text (`ask {request}`); anything more complex stays pass-through. The Skills tab labels those actions **pass-through (any args)**.
- Timeline: treat documented xAI keepalive and session events (`ping`, `conversation.created`, `conversation.item.input_audio_transcription.updated`) as known so they do not appear as unknown `upstream_event` rows.

## [0.3.5] - 2026-10-15

- Timeline records xAI provider tools (`web_search`, `x_keyword_search`, …) that Grok reports as function calls after the spoken answer, plus `response.done` status and a compact output list. Unknown non-delta upstream events are logged once per type per response (bounded). The tablet still sees the same forwarded events as before.
- Session waterfall bars are positioned on a time axis: left offset is `t / session length`, spans are speech, end-of-speech → first audio, speaking, HA tool, and memory; other events are ticks. Daily cost chart bars show a hover/tap tooltip (date, sessions, minutes, realtime + summary + total).

## [0.3.4] - 2026-10-15

- Session detail on the Status tab is a per-session timeline/waterfall: connect/auth, KS `session.update`, upstream xAI open, `session.updated` (ready); per turn speech start/stop (VAD), user and assistant transcripts when xAI sends them, end-of-speech → first audio, first audio → `response.done`, total response time, audio seconds; per tool call name, arguments and result (truncated, full payload on click), model `function_call` → relay dispatch, Home Assistant time, tool result → next first audio; memory-summary timing and tokens; token usage per `response.done`; session end reason (`end_conversation`, client close, idle). Stored in `stats.db` next to existing session rows (payloads truncated, retention unchanged, secret-like keys and known secrets redacted).
- Optional per-session cache for identical read-only skill calls (`help` / `catalog` / `get` / `list`, same name/action/args). Default on; a write to that skill invalidates it. Cache hits are marked on the timeline so duplicate catalog/help round-trips are visible.
- Short-term memory stores tool lines as `turn_on ok` or `skill dashboard.set failed` (no args or results). Empty summaries are not saved. Existing bloated rows are compacted the same way when injected into the next session, and rows with empty summaries are skipped.

## [0.3.3] - 2026-10-15

- Identify Kiosk Satellite tablets by connecting IP when `?device=` and the KS instruction name are missing: each satellite's Home Assistant `configuration_url` host and ipv4 address sensor, refreshed with the cache. Area fallback prefers a KS tablet over a Voice PE in the same room.
- Tablets tab shows detected IPs, lists unmatched session remotes with **Assign to tablet**, and stores the map in settings (hot-applied). Logs which method matched and the remote IP; unknown sessions are labeled `unknown tablet (<ip>)`.
- Document optional Endpoint `&device=<name>` in DOCS.md.

## [0.3.2] - 2026-10-15

- Fix the relay panel inline script so the Status / Settings / Skills / Tablets / Test tabs render. A stray semicolon after the Settings “copy secret” handler left a bare `else` (`Unexpected token else`) and Chrome stopped the script before `nav()` ran.
- Guard: CI and pytest extract every inline `<script>` from served HTML and run `node --check`. Client tests load the panel in jsdom and assert the tab buttons render.

## [0.3.1] - 2026-10-15

- The Grok Voice sidebar opens on the relay panel (Status / Settings / Skills / Tablets). The old browser duplex client is a **Test** tab (also at `/test/`). The panel websocket stays at `/relay/ws`, distinct from the voice socket.
- The panel always runs through Home Assistant ingress, even when **Kiosk Satellite relay secret** is empty. Only the tablet listener on port 8766 waits for a secret. Status says when the relay is off and why.
- Set or generate the relay secret on the Settings tab; it applies without an add-on restart. The value is masked, never logged, and never returned in full except once after Generate so you can paste it into Kiosk Satellite.
- Panel load no longer sits on “Loading…”: socket errors and close reasons are shown, pending calls time out, and silent retry stops after a few attempts with a Retry button.

## [0.3.0] - 2026-10-15

- The add-on is now a realtime voice relay for Grok Voice in front of Kiosk Satellite. Set the new **Kiosk Satellite relay secret** option (the xAI key stays in the add-on), then on each tablet set the xAI **Endpoint** to `ws://<ha-host>:8766/v1/realtime?model=grok-voice-latest`, the API key to the secret, **Tools** to None and history to its minimum.
- Session rewrite: reasoning effort, end of speech (xAI's default instead of KS's 500 ms), the local date and time with explicit "today / tonight / tomorrow" rules (refreshed after midnight or ten minutes), and day labels on dates in tool results (UTC timestamps shown as local time with "today" / "tomorrow").
- Built-in tools run in the relay against Home Assistant (on/off, lights, covers, locks, climate, fans, timers, lists, state lookup) instead of ~58 MCP tools; KS's `end_conversation` passes through.
- Skills: one `skill` tool with a short index and `action=help`, backed by scripts, services, relay handlers (media) or Home Assistant's `script.skill`. Generic seed; on first run the relay imports `script.skill` from Home Assistant if present; import and export as JSON.
- Short-term memory per tablet or house-wide, summarized by a fast xAI text model after each session.
- New **relay** page in the Grok Voice panel (ingress only): settings with live reload and per-tablet overrides, skill editor, status with per-session latency and tool timings, memory with a clear button.
- Usage and cost estimates on the Status tab: per-session records in `/data/stats.db` (duration, turns, latency median / p90, tools, xAI token usage, summary tokens), today / 7 / 30-day totals, per-tablet cost and latency, a daily chart, drill-down, configurable prices, and optional `sensor.grok_relay_*` entities in Home Assistant.
- Measured on one setup, end of speech to first audio: 2.5 s → 1.0 s for a light command, 1.8 s → 0.75 s for a question.
- README and DOCS rewritten around the relay; the browser and injected duplex client is documented as legacy.

## [0.2.26] - 2026-10-01

- Narrow dismiss hang-up to clear thank-you / that's-all closers (`thank you`, `thanks`, `that's all`, `that's it`, `that'll do`, `goodbye`, `bye`, `good night`, `I'm done`, `we're done`, `we're good`, `all set`, and close variants). Drop weaker phrases that caused false hang-ups (`stop listening`, `you can go`, `never mind`, `that's enough`, `that's everything`, `carry on`, `go now`).
- Stop offering Q&A follow-ups. After sports/news/trivia, Grok gives a short first answer and waits. Prompt text no longer steers it to ask “Want his term?” or “Anything else?”. Home-command hang-up, overlay tap, and idle are unchanged; Q&A still does not hang up on its own.

## [0.2.25] - 2026-09-30

- Auto-pick this kiosk's Home Assistant area when Kiosk Satellite does not expose area fields. Duplex auth no longer sticks on `Area (none) source=fallback` for a tablet whose HA device already has an area (Dining Room on "Dining Room Dashboard"): match `name_by_user` and `name` (including `{Room} Dashboard` vs `{Room}`), then unique ESPHome / `assist_satellite` ids, and wait briefly for registries on the session path once `callWS` is ready. Listening-on-wake, hang-up, Q&A, `duplex_lan_host`, and overlay streaming are unchanged. No per-tablet inject or global `default_area` required when the HA device has an area.

## [0.2.24] - 2026-09-30

- Add add-on option **Duplex LAN host** (`duplex_lan_host`) so Kiosk Satellite tablets can open duplex on a Home Assistant LAN IP (for example `192.168.1.50`) instead of falling through to `homeassistant.local`. Fire tablets often fail that mDNS name (`ERR_NAME_NOT_RESOLVED`). Leave the option blank to keep automatic discovery (HA connection host, ingress, script origin). The value is served at `/kiosk-config` and injected into `grok-voice.js` / `kiosk-boot.js`. Hang-up, Q&A, overlay streaming, and Listening-on-wake are unchanged.

## [0.2.23] - 2026-09-30

- Fix the attic kiosk wake race after snappy Listening-on-wake: native Assist `vs_cancel` could close the first duplex (and its AudioContext) while `getUserMedia` was still in flight, so the mic graph was built on a closed context (`MediaStreamAudioSourceNode` / `ScriptProcessor` / `Gain` / “Connecting nodes after the context has been closed”), the overlay flashed Listening then disappeared, and KS re-armed without a live session. Ensure a fresh open AudioContext (or resume) before attaching the mic graph; abort instead of connecting after close; retry a duplex that dies during open without dismissing the overlay. Listening still paints immediately. Hang-up and Q&A policy are unchanged. A first-boot area fallback no longer blocks a later Home Assistant attic/area lookup.

## [0.2.22] - 2026-09-30

- Public README for first-time Home Assistant users: clear install / configure / Open Web UI / kiosk / hang-up sections, CI and Release badges, and no private-repo “does not appear” caveats.

## [0.2.21] - 2026-09-30

- Stream overlay Grok and You: text **letter by letter** with a short slide/fade instead of dumping whole token chunks. A typewriter buffer drains new characters as they arrive and catches up when the stream ends so the line does not lag seconds behind TTS.
- Show the full-screen **Listening** overlay as soon as the wake word fires. Do not wait for native Assist `vs_cancel` lookup or the old 400ms dashboard-unpause settle before painting or opening duplex; mic capture still starts with the socket. Hang-up and Q&A policy are unchanged.

## [0.2.20] - 2026-09-30

- Stop Q&A monologues. After sports/events/history/news, Grok gives a **short first answer**, may ask **one** brief offer of more (“Want his term?”), then **stops and waits**. It must not keep talking and answer that offer itself. Hang-up is unchanged from 0.2.18: home-control success, thank you / goodbye, overlay tap, and idle still close the duplex; Q&A stays open quietly.

## [0.2.19] - 2026-09-30

- Public-repo defaults: blank **Default area** and **Home location**, generic voice instructions (no Attic / HomePod Mini, and no “ask a follow-up” after Q&A — answer then listen). Kiosk LAN fallback uses `homeassistant.local` instead of a private IP. README is a first-run guide; DOCS.md no longer describes a personal LAN.

## [0.2.18] - 2026-09-30

- Restore goodbye / thank-you hang-up after 0.2.16. Saying **thank you**, **thanks**, **that's all**, or similar still closes the duplex after the ack TTS drains, even if Grok's ack asks a soft “Anything else?”. Overlay tap-dismiss and home-command hang-up are unchanged. Sports/events/Q&A follow-ups without a closer still stay open (`end_session(reason=dismiss)` is ignored unless a goodbye phrase was detected).

## [0.2.17] - 2026-09-30

- Always inject add-on **Home location** (`home_location`, for example `Springfield, IL`) into every duplex `session.update` so Grok can answer where you live, what’s nearby, and weather here without asking. Customized **Instructions** no longer omit that block. Leave the option blank if unset.

## [0.2.16] - 2026-09-30

- Keep the duplex open after sports/events/history/Q&A follow-ups. `end_session` no longer hangs up when Grok asked a question (or used `reason=dismiss` without a goodbye phrase). Home-command hang-up, tap-dismiss, goodbye phrases, TTS drain, and KS re-arm are unchanged. Idle still ends a truly silent session; a follow-up question adds 15s of grace so the user can hear the question and answer, and the default idle is 30s.

## [0.2.15] - 2026-09-30

- Hang up the duplex only after a **home device / in-home media** action (lights, garage, locks, climate, covers, play/pause/volume) or an explicit goodbye / tap-dismiss. Sports, events, history, news, and other Q&A stay open: Grok is told to answer and ask a short follow-up, and `end_session` with `reason=command` is ignored unless a home-control tool succeeded this turn. Idle timeout, goodbye phrases, overlay tap, and TTS drain before hang-up are unchanged.

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

- Name the city in session location context. Add-on **Home location** (`Springfield, IL`) is expanded to **Springfield, Illinois** in the Grok instructions so answers can say the city, not only a vague home/GPS string.
- Keep short per-device conversation memory across wakes within the TTL. History is keyed by a stable tablet id (not area fallback), written as turns complete (including xAI `updated` transcripts after VAD stop), and reinjected on the next `session.update` both as instructions and as `conversation.item.create` messages so follow-ups like “do I have a meeting today?” use what the user just said, not only the calendar tool. Attic vs dining stay separate. Goodbye still clears that device; idle hang-up keeps the turns (~8 minutes).
- Increase local mic pre-roll to **900ms** and flush it when the duplex session is `ready` (not at WebSocket open). Server VAD `prefix_padding_ms` is **800ms** and threshold **0.35**. getUserMedia is still not delayed.
- Hang up after a one-shot home command or a dismissal without an exact goodbye phrase. Grok gets a local **`end_session`** tool (`reason=command` or `reason=dismiss`). After a short ack, the duplex ends and wake-word listening resumes (same path as goodbye). Phrase matching also accepts “you can go now” / “thanks I’m done”. Do not auto-end mid-multi-step or while asking a question. Command hang-up keeps the 8-minute tablet history; dismiss/goodbye still clears it.
- Kiosk overlay is full-screen (blur + dim over the dashboard, Listening/Speaking on top) with larger conversation type (`clamp(26px, 4.2vw, 40px)`) so Fire tablets can read it across a room. User and Grok text update as transcript/response deltas arrive, not only on the final chunk. Each tablet still has its own overlay and session.

## [0.2.9] - 2026-09-30

- Inject the current local date/time and home location into every Grok session at open (server-side `session.update` instructions). Timezone and GPS come from Home Assistant `/api/config` plus `zone.home`; the add-on **Home location** option supplies a city/ZIP such as Springfield, IL. The clock is computed when the session starts, not baked into the add-on.
- Keep a short per-device conversation memory after hang-up so a new wake on the same satellite can follow up. Keyed by kiosk device (then area); TTL defaults to 8 minutes (`conversation_memory_ttl_seconds`). Cleared when that timer expires or the user says goodbye.
- Soften server VAD slightly and include 400ms of audio pre-roll (`prefix_padding_ms`) so the first syllable after snappy listen-start is less likely to be clipped. Area lookup stays off the wake path.

## [0.2.8] - 2026-09-30

- Serve the kiosk IIFE from the add-on at a stable URL (`http://<HA-LAN>:8080/grok-voice.js`, same port as duplex). The attic Kiosk Satellite inject is a one-time bootstrap; updating the add-on refreshes the client without pasting the full script into Remote Admin. Dining-room dashboards that never load the bootstrap stay untouched.

## [0.2.7] - 2026-09-30

- Restore snappy listen-start on the attic kiosk inject. 0.2.6 awaited Home Assistant device/entity/area registry lists (and `getDeviceInfo`) on every wake before `getUserMedia` and the duplex socket. The inject now prefetches the area at boot and sends the cached/explicit/Attic fallback on auth immediately. Registry lookups run in parallel in the background. The duplex WebSocket opens while the mic is claimed.
- Attach Assist media, todo, and Mealie tools on a blank allowlist (`HassMediaSearchAndPlay`, pause/volume/next, `HassListAddItem`, `HassGetState`, plus `music_assistant` / `media_player` / `todo` / `mealie` domains). Bare "play music" / "play X" from a tablet targets Music Assistant in the session area and does not ask which speaker.

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
