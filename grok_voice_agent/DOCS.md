# Grok Voice Agent

Home Assistant add-on that talks to Grok Voice Think Fast 2.0 and runs Assist tools through the local MCP server. Open the add-on page in a browser, or load the kiosk client on a Kiosk Satellite dashboard. The xAI key stays in the add-on.

This page is the detailed companion to the [root README](../README.md). Start there for install and first-run configuration.

## Install from the Add-on store

Home Assistant clones this GitHub URL as a store repository. The repo root has `repository.yaml`; the add-on itself is the `grok_voice_agent/` folder (`config.yaml`, `Dockerfile`, …).

The GitHub repository must be **public**. The add-on store does not log into GitHub, so a private clone will not show **Grok Voice Agent**.

1. In Home Assistant: **Settings → Add-ons → Add-on store → ⋮ → Repositories**.
2. Add `https://github.com/fabianluque/ha-grok-voice` and save.
3. On the store page, open **Grok Voice Agent** and install it. Home Assistant builds the image locally from the Dockerfile.
4. Open the add-on **Configuration**, set **xAI API key**, and save. Do not put that key in the browser UI.
5. Optionally set **Home location** (city / region / ZIP) and **Default area**. Both ship **blank**. Leave **Home location** empty until you fill it in.
6. Install the official **Model Context Protocol Server** integration and expose the entities Assist may control.
7. Leave the MCP URL blank. It uses `http://supervisor/core/api/mcp` with the Supervisor add-on token (`SUPERVISOR_TOKEN`). Leave the long-lived token blank too. Paste a long-lived token only if the log shows MCP HTTP 401. A blank token is not sent. The startup log should say `mcp_auth=supervisor`.
8. Start the add-on.

A blank tool allowlist is lights (`HassTurnOn`, `HassTurnOff`, `HassLightSet`), live context (`GetLiveContext`, `GetDateTime`, `HassGetState`), media / Music Assistant (`HassMediaSearchAndPlay`, pause/volume/next, `play_media`, and the `media_player` / `music_assistant` domains), todo / shopping lists (`HassListAddItem` and the `todo` domain), and Mealie (`mealie`). Home Assistant 2026.9 prefixes those with the integration domain (`intent__HassTurnOn`, `music_assistant__play_media`, `todo__HassListAddItem`, `mealie__get_mealplan`). The blank allowlist matches the bare name, the prefixed name, and those domains. The voice-session log reports the names that were attached. Set `*` to offer every MCP tool.

After a client UI or kiosk-client change, run `npm run build` in `client/` so `grok_voice_agent/www` is current (`index.html` / `ui.js` plus `grok-voice.js` / `kiosk-boot.js`), then update/rebuild the add-on so `/app/www` is in the image.

The add-on serves the mic UI on dashboard ingress (port 8099, **Open Web UI**) and on the debug port (`8080/tcp`). Open Web UI uses the signed-in Home Assistant ingress session, so it does not ask for a token. The debug port still sends an optional long-lived access token on the first WebSocket message. The same debug port also serves the kiosk IIFE at `http://<HA-LAN>:8080/grok-voice.js` (see [Kiosk Satellite](#kiosk-satellite)).

## Local `/addons` (optional, for development)

To iterate without the GitHub store, copy `grok_voice_agent/` into Home Assistant’s local add-ons directory as `/addons/grok_voice_agent` (Samba share `addons`, SSH, or similar). Local add-ons do not use `repository.yaml`. Rebuild with `ha apps rebuild` / reinstall after you change the add-on or the packaged UI.

## Test from a Mac browser

The UI needs a **secure context** for `getUserMedia` (HTTPS, or `localhost`). `http://homeassistant.local:8080` is not secure, so Chrome and Safari will block the microphone unless you use one of the paths below.

### Home Assistant Open Web UI / ingress

1. Start **Grok Voice Agent**.
2. Stay signed in to Home Assistant and choose **Open Web UI**, or open **Grok Voice** in the sidebar. That panel creates the ingress session, then loads the add-on. Do not paste a token.
3. Click **Start talking** and allow the microphone.
4. If the page says the session was rejected, a long-lived token field appears. Paste a token there, or open **Open Web UI** again.
5. If the sidebar iframe blocks the mic, use **Open Web UI** so Home Assistant opens the panel itself.

Opening `https://<your-home-assistant-host>/api/hassio_ingress/<token>/` directly returns Supervisor **401: Unauthorized** when the browser has no live `ingress_session` cookie. Signing in remotely does not create that cookie. Use **Open Web UI** (it opens `/app/<add-on slug>` and sets the cookie) instead of a bookmarked ingress link. The cookie lasts about 15 minutes unless that panel keeps it alive.

Prefer this path when Home Assistant itself is HTTPS (Nabu Casa or a local certificate). The WebSocket stays on the ingress URL; no extra port on the tablet.

### LAN debug port `:8080`

1. Confirm the add-on maps `8080/tcp` (already in `config.yaml`) and has started.
2. On the Mac, create a Home Assistant **long-lived access token** (Profile → Security) and paste it into the UI. Do not put an xAI key in the browser. Optionally check **Remember this token in this browser**.
3. Open a secure context to port 8080:

   - **SSH tunnel (simplest on a Mac):**
     `ssh -L 8080:127.0.0.1:8080 homeassistant.local`
     then visit `http://127.0.0.1:8080` (secure as localhost).
   - **HTTPS:** open the UI through a TLS reverse proxy to `:8080`.
   - **Chrome HTTP exception (last resort):**
     `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
     add `http://homeassistant.local:8080` or `http://<ha-ip>:8080`, relaunch, then open that URL.

4. Click **Start talking**, allow the microphone, and speak. Status should move from connecting to **Listening**. Stop or wait for the idle timeout to end the session.

The debug page talks to `ws://<that-host>:8080/` with the same `{ "type": "auth", "token": "..." }` message the kiosk client sends.

## Kiosk Satellite

Leave Voice Satellite's wake word on. Kiosk Satellite only detects a wake word while that detection is enabled; do not set it to Disabled. Kiosk Satellite's own voice runtime is often native (`voice.runtime=native`). That path starts Assist inside the app and pauses the dashboard, which is separate from the Voice Satellite card. The loaded kiosk script still blocks the card (`onWakeAction`, the blur overlay, and an STT `pipeline.start`). It also calls this kiosk's ESPHome `vs_cancel` action, matched by the kiosk device name, so the native Assist turn stops and the WebView stays awake for Grok. The Grok overlay paints **Listening** immediately on wake; `vs_cancel` runs in parallel and duplex no longer waits on a settle delay (a pause that closes the first socket is retried). It does not call any other kiosk's action. Enable the inject only on the dashboards that should use Grok.

### Stable client URL

The running add-on always serves the current kiosk IIFE (classic script, not an ES module) next to the mic UI:

| URL | When to use it |
| --- | --- |
| `http://<HA-LAN>:8080/grok-voice.js` | **Kiosk / Lovelace.** Same host and port as duplex `ws://<HA-LAN>:8080/`. No Home Assistant token on this GET (a `<script src>` cannot send one). Duplex still sends the long-lived / session token on the first WebSocket message. |
| `http://<HA-LAN>:8080/kiosk-boot.js` | Tiny loader with the same host discovery as duplex. Optional; the one-line inject below loads `grok-voice.js` directly. |
| `/grok-voice.js` on ingress `:8099` / Open Web UI | Same file, but only inside an ingress session. Lovelace has **no** `ingress_session` cookie, so `https://<HA>/api/hassio_ingress/<token>/grok-voice.js` returns Supervisor **401**. Do not use that path from a dashboard. |

Replace `<HA-LAN>` with the hostname or IP the tablet uses to reach Home Assistant (`homeassistant.local` on many HAOS installs, or a LAN IP). Example:

`http://homeassistant.local:8080/grok-voice.js`

CORS is allowed (`Access-Control-Allow-Origin: *`) so a dashboard origin (`http://127.0.0.1:2325` Kiosk Satellite proxy, or `http://<HA-LAN>:8123`) can load the file. Classic `<script src>` works without CORS; the header is for `fetch` / module resources. `Cache-Control: no-cache` makes the tablet revalidate after an add-on update.

If every discovered Home Assistant URL is loopback (typical on the Kiosk Satellite proxy), the client falls back to `homeassistant.local`. Set `window.GROK_VOICE_URL` / `window.GROK_VOICE_SCRIPT` when that name does not resolve.

### Enable once on a kiosk

Do this in Kiosk Satellite **Remote Admin** (the tablet’s `:2324` admin UI) for each dashboard that should run Grok.

1. Start **Grok Voice Agent** so port `8080` is listening on the Home Assistant host.
2. Open **Browser → Inject JavaScript on the HA dashboard**.
3. Replace that field with this bootstrap (not the 20k+ client). Optional overrides (`GROK_VOICE_AREA`, `GROK_VOICE_URL`, …) can sit above it.

```javascript
(() => {
  const s = document.createElement("script");
  s.src = "http://homeassistant.local:8080/grok-voice.js";
  document.documentElement.appendChild(s);
})();
```

4. Reload the kiosk. The dashboard stays a normal Lovelace page; the script tag is cross-origin to `:8080` and does not need a Supervisor ingress cookie.

After that, **update the add-on** to refresh the client. The next dashboard load fetches the new `grok-voice.js`. Do not paste the full file into Remote Admin again unless the Home Assistant host or debug port changes.

To load the host-discovering bootstrap instead of hardcoding a host, point `s.src` at `http://homeassistant.local:8080/kiosk-boot.js`, or set `window.GROK_VOICE_SCRIPT` to another URL before the script tag. `window.GROK_VOICE_DEBUG_PORT` still selects the duplex port.

The tablet user must be able to reach `http://<HA-LAN>:8080/` on the LAN. Open Web UI / ingress is not required for the kiosk path.

The microphone stays open while Grok is speaking. Talking over a reply flushes playback in the browser. A later tool call or second TTS generation does **not** cut the sentence already playing; new audio waits until that reply finishes. After wake, a **full-screen** conversation overlay covers that tablet's dashboard (blur plus a semi-transparent dim) as soon as the wake word fires — **Listening** is painted before the duplex socket is `ready`. Listening / Speaking stays visible at the top. Conversation text uses `clamp(18px, 2.9vw, 28px)`. The overlay is **this wake only**: user lines stream as xAI `conversation.item.input_audio_transcription.updated` snapshots (enabled with `audio.input.transcription.model=grok-transcribe`; xAI does not send OpenAI-style `.delta` events). Cumulative and **revised** snapshots for the same ASR item update one live You: line (including after VAD stop / `completed`); a new utterance after a true turn boundary is a new You: line. Grok lines update in place as `response.audio_transcript.delta` events stream in. New characters **slide/fade in letter by letter** from a typewriter buffer rather than appearing as whole token dumps; the buffer catches up when the stream ends so text does not lag behind speech. Reinjected short-history turns are not drawn. Tap the overlay to hang up (ack TTS still plays if Grok is mid-reply). The overlay is per kiosk inject, so two tablets stay independent. It disappears when the session ends.

Each kiosk session is scoped to **that tablet's Home Assistant area** so "turn on the lights" or "play music" targets this room, not the whole house. Bare "play X" / "play music" uses Music Assistant on this area's player and does not ask which speaker. The add-on fills Assist `area` / `area_id` (and `target.area_id` on Music Assistant `play_media`) when Grok omits them, and the voice prompt says not to ask which lights or which speaker.

The kiosk **prefetches** the HA/KS area when the inject loads. Wake uses the cached or explicit area immediately (no invented room name), mounts the overlay on the wake event, and opens the mic and WebSocket together without waiting for Assist cancel lookup. Conversation memory uses a stable per-tablet id from `localStorage` on every auth, including the first wake before `getDeviceInfo` returns.

Resolution order:

1. Optional inject override: `window.GROK_VOICE_AREA` (name) and `window.GROK_VOICE_AREA_ID` (slug). Add this near the top of the inject only if HA/KS lookup is wrong.
2. Area fields on Kiosk Satellite `getDeviceInfo()` when the app exposes them (`area`, `area_name`, `area_id`, `assist_area`, …).
3. The Home Assistant area assigned to this kiosk device (same device name as `getDeviceInfo().name`), including an `assist_satellite` entity area when that entity has its own area.
4. Add-on configuration **Default area** / **Default area ID** (`default_area`, `default_area_id`). Both ship blank. Set a name if Open Web UI or a failed lookup should still have a room; set `default_area_id` if Assist needs the slug and a blank id is not enough.
5. If the name is set and the id is still blank, the add-on derives a slug (`Kitchen` → `kitchen`).

A second tablet with the same inject resolves **its own** KS/HA area. Do not hardcode a room in the inject unless you are forcing an override. The add-on default is only the fallback when the kiosk did not send an area (Open Web UI, or lookup failed).

### Session date, location, and short memory

At each duplex session open the add-on writes a fresh block into the Grok `session.update` instructions (every client: kiosk, Open Web UI, port 8080):

- **Current local date/time** from Home Assistant's timezone (`/api/config` `time_zone`). Computed at session start, not stored in the add-on image.
- **Home location** for local events: add-on **Home location** (`home_location`, for example `Austin, TX`) is written into every `session.update` as the place the user lives (US state abbreviations are expanded, so `Austin, TX` also becomes `Austin, Texas`) plus HA `location_name`, country, GPS, and `zone.home` when those APIs answer in time. Generic HA names like `Home` are not used as the city, and GetLiveContext must not override it. **Leave the option blank** if you have not set a city: Grok will not know where you live and may ask. Fetch runs in parallel with MCP `tools/list` so it does not sit on the wake path. Customized **Instructions** stay the spoken persona; they do not replace this location block.

Short **conversation memory** is Assist-style, in the add-on process only (not forever, not across add-on restarts):

- **Key:** a stable per-tablet device id from the kiosk (`localStorage` `grok-voice-device-id`, sent on auth even before `getDeviceInfo` returns). Open Web UI uses `device=web` so it does not share a tablet's history. Two kiosks stay separate. Area is used for lights/music, not as the memory key, so a first wake that still has no area does not mix another tablet's follow-ups.
- **Write:** final user/assistant transcripts during the session (and the last partial user transcript when server VAD stops speech). Not only at hang-up.
- **Reinject:** the next wake on that device, within the TTL, gets the recent turns in `session.update` instructions **and** as `conversation.item.create` items so Grok can answer from spoken context even if a calendar tool returns empty. Those items stay in the model session only; the on-screen overlay starts blank and shows this wake’s user/assistant streaming.
- **TTL:** `conversation_memory_ttl_seconds` (default 480, eight minutes; configurable 60–3600). Sliding: a new session on that key refreshes the timer.
- **Clear:** TTL expiry, or a goodbye hang-up (`thank you` / `that's all` / `goodbye`). An idle hang-up keeps the recent turns so a wake two minutes later can continue the conversation.

Mic audio that arrives before the duplex session is `ready` is capped to the last **900ms** (pre-roll) and flushed then. Server VAD uses `prefix_padding_ms=800` and threshold `0.35` so the first syllable after listen-start is less likely to be cut. `idle_timeout_ms` and `silence_duration_ms` are still not set. getUserMedia is not delayed.

Say **thank you**, **thanks**, **that's all**, **that's it**, **goodbye**, **you can go now**, **thanks I'm done**, or **stop listening** to hang up. A longer utterance that *ends* with one of those still hangs up (`oh, that's great, thank you`). The same words in the middle of a request (`thank you for turning on the lights`) do not. Grok also has an **`end_session`** tool: after a brief ack it hangs up following a successful **home device or in-home media** action (lights, garage, locks, climate, covers, play/pause/volume) or when you dismiss it with a goodbye phrase. Overlay tap-dismiss still hangs up. After sports, news, events, history, or other questions it gives a **short first answer**, may ask **one** brief offer of more (“Want his term?”), then **stops and waits**. It must not keep talking and answer that offer itself, and it should not hang up just because that turn was Q&A. The add-on ignores `end_session` with `reason=command` unless a home-control tool succeeded this turn, and ignores `reason=dismiss` unless a goodbye phrase was detected (so a Q&A offer like `Want last night's highlights?` stays open). If you did say thank you / goodbye, it hangs up after the ack even when that ack asks “anything else?”. It should not hang up during a multi-step task or while asking a clarifying question. The spoken ack is allowed to finish: hang-up waits until queued assistant audio has played, then closes the duplex. The session also ends after `idle_timeout_seconds` of silence once Grok has finished and you are not mid-utterance (default 30s; a follow-up question adds 15s so you can hear it and answer). That timer uses xAI server VAD (`speech_started` / `speech_stopped` / `response.done`). xAI's `turn_detection.idle_timeout_ms` is not used — that option only triggers a proactive check-in, it does not close the session.

When the session ends, the inject waits for any remaining Grok audio to finish, then stops the browser microphone, hides Assist chrome, and calls Kiosk Satellite `setWakeWordActive(true)` so on-device wake listening resumes. The next wake word starts a fresh duplex session. If the dashboard or inject reloads while a duplex session is still open, the inject hangs up that orphan session and re-arms KS wake on `pagehide`/`beforeunload` and again when the script boots, so the wake word is not left dead.
