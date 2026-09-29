# Grok Voice Agent

Home Assistant add-on that talks to Grok Voice Think Fast 2.0 and runs Assist tools through the local MCP server. Open the add-on page in a browser (Mac, tablet, or the attic Kiosk Satellite dashboard). The xAI key stays in the add-on.

## Add-on

1. In Home Assistant, add this repository under Settings → Add-ons → Add-on store → Repositories.
2. Install **Grok Voice Agent** and set the xAI API key.
3. Install the official **Model Context Protocol Server** integration and expose the entities Assist may control.
4. Leave the MCP URL blank. It uses `http://supervisor/core/api/mcp` with the add-on token. Paste a long-lived token only if the log shows MCP HTTP 401.
5. Start the add-on. Rebuild after a client UI change (`npm run build` in `client/`, then `ha apps rebuild` / reinstall) so `/app/www` is in the image.

A blank tool allowlist is `HassTurnOn`, `HassTurnOff`, `HassLightSet`, `GetLiveContext`, and `GetDateTime`. Set `*` to offer every MCP tool.

The add-on serves the mic UI on dashboard ingress (port 8099, **Open Web UI**) and on the debug port (`8080/tcp`). Voice still authenticates with a Home Assistant access token on the first WebSocket message; that handshake is unchanged.

## Test from a Mac browser

The UI needs a **secure context** for `getUserMedia` (HTTPS, or `localhost`). `http://homeassistant.local:8080` is not secure, so Chrome and Safari will block the microphone unless you use one of the paths below.

### Home Assistant Open Web UI / ingress

1. Start **Grok Voice Agent**.
2. Open the add-on and choose **Open Web UI**, or open **Grok Voice** in the sidebar.
3. If the page asks for a token, the iframe could not read the HA session. Use a user who can open this add-on's ingress (an administrator can).
4. Click **Start talking** and allow the microphone.
5. If the HA sidebar iframe blocks the mic, open the ingress URL in its own tab (same origin as Home Assistant). Same-origin pages can reuse `hassTokens` / the parent `hass` connection automatically.

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

1. On that tablet, turn off the native Assist answer path so this script is the only wake-word listener.
2. Build `client/dist/grok-voice.js` (`npm test` then `npm run build` inside `client/`).
3. Paste that file into Kiosk Satellite's **Inject JavaScript on the HA dashboard** field and reload.
4. The tablet user must be able to open this add-on's ingress. An administrator can.

The microphone stays open while Grok is speaking. Talking over a reply flushes playback in the browser. The wake word is armed again only when the session ends.
