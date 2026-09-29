# Grok Voice Agent

Home Assistant add-on that talks to Grok Voice Think Fast 2.0 and runs Assist tools through the local MCP server. Open the add-on page in a browser (Mac, tablet, or the attic Kiosk Satellite dashboard). The xAI key stays in the add-on.

## Install from the Add-on store

Home Assistant clones this GitHub URL as a store repository. The repo root has `repository.yaml`; the add-on itself is the `grok_voice_agent/` folder (`config.yaml`, `Dockerfile`, …).

The GitHub repository must be **public**. The add-on store does not log into GitHub, so a private clone will not show **Grok Voice Agent**.

1. In Home Assistant: **Settings → Add-ons → Add-on store → ⋮ → Repositories**.
2. Add `https://github.com/fabianluque/ha-grok-voice` and save.
3. On the store page, open **Grok Voice Agent** and install it. Home Assistant builds the image locally from the Dockerfile.
4. Open the add-on **Configuration**, set **xAI API key**, and save. Do not put that key in the browser UI.
5. Install the official **Model Context Protocol Server** integration and expose the entities Assist may control.
6. Leave the MCP URL blank. It uses `http://supervisor/core/api/mcp` with the Supervisor add-on token (`SUPERVISOR_TOKEN`). Leave the long-lived token blank too. Paste a long-lived token only if the log shows MCP HTTP 401. A blank token is not sent. The startup log should say `mcp_auth=supervisor`.
7. Start the add-on.

A blank tool allowlist is `HassTurnOn`, `HassTurnOff`, `HassLightSet`, `GetLiveContext`, and `GetDateTime`. Home Assistant 2026.9 prefixes those with the integration domain (`intent__HassTurnOn`, `intent__HassTurnOff`, `light__HassLightSet`, `homeassistant__GetLiveContext`). The blank allowlist matches the bare name and the prefixed name, and the voice-session log reports the names that were attached. Set `*` to offer every MCP tool.

After a client UI change, run `npm run build` in `client/` so `grok_voice_agent/www` is current, then update/rebuild the add-on so `/app/www` is in the image.

The add-on serves the mic UI on dashboard ingress (port 8099, **Open Web UI**) and on the debug port (`8080/tcp`). Open Web UI uses the signed-in Home Assistant ingress session, so it does not ask for a token. The debug port still sends an optional long-lived access token on the first WebSocket message.

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

Opening `https://<your-nabu-casa-host>/api/hassio_ingress/<token>/` directly returns Supervisor **401: Unauthorized** when the browser has no live `ingress_session` cookie. Signing in to Nabu Casa does not create that cookie. Use **Open Web UI** (it opens `/app/<add-on slug>` and sets the cookie) instead of a bookmarked ingress link. The cookie lasts about 15 minutes unless that panel keeps it alive.

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

Leave Voice Satellite's wake word on. Kiosk Satellite only detects a wake word while that detection is enabled; do not set it to Disabled. On the attic tablet, Kiosk Satellite's own voice runtime is native (`voice.runtime=native`). That path starts Assist inside the app and pauses the dashboard, which is separate from the Voice Satellite card. The injected script still blocks the card (`onWakeAction`, the blur overlay, and an STT `pipeline.start`). It also calls this kiosk's ESPHome `vs_cancel` action, matched by the kiosk device name, so the native Assist turn stops. The dashboard WebView is still paused for a moment after that call, so the inject waits until Home Assistant's websocket answers again before opening Grok. A working wake logs `[Grok Voice] Duplex session open` and then `[Grok Voice] Duplex session stayed open`. It does not call any other kiosk's action. Do not change the dining room dashboard or its inject.

1. In `client/`, run `npm test` then `npm run build:kiosk`. That writes `client/dist/grok-voice.js`.
2. In Kiosk Satellite **Remote Admin**, open the **attic** dashboard only. Go to **Browser → Inject JavaScript on the HA dashboard**.
3. Replace that field with the entire contents of `client/dist/grok-voice.js` (the whole minified file). Leave the dining room kiosk's inject field unchanged.
4. Reload the attic kiosk.
5. The tablet user must be able to open this add-on's ingress. An administrator can.

The microphone stays open while Grok is speaking. Talking over a reply flushes playback in the browser. The wake word is armed again only when the session ends.
