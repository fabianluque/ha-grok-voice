# Grok Voice Agent

Home Assistant add-on that talks to Grok Voice Think Fast 2.0 and runs Assist tools through the local MCP server. The attic tablet (or any Kiosk Satellite dashboard) intercepts the wake word and keeps a full-duplex browser session. The xAI key stays in the add-on.

## Add-on

1. In Home Assistant, add this repository under Settings → Add-ons → Add-on store → Repositories.
2. Install **Grok Voice Agent** and set the xAI API key.
3. Install the official **Model Context Protocol Server** integration and expose the entities Assist may control.
4. Leave the MCP URL blank. It uses `http://supervisor/core/api/mcp` with the add-on token. Paste a long-lived token only if the log shows MCP HTTP 401.
5. Start the add-on. The dashboard connects through ingress, so the tablet does not open a separate port.

A blank tool allowlist is `HassTurnOn`, `HassTurnOff`, `HassLightSet`, `GetLiveContext`, and `GetDateTime`. Set `*` to offer every MCP tool.

## Kiosk Satellite

1. On that tablet, turn off the native Assist answer path so this script is the only wake-word listener.
2. Build `client/dist/grok-voice.js` (`npm test` then `npm run build` inside `client/`).
3. Paste that file into Kiosk Satellite's **Inject JavaScript on the HA dashboard** field and reload.
4. The tablet user must be able to open this add-on's ingress. An administrator can.

The microphone stays open while Grok is speaking. Talking over a reply flushes playback in the browser. The wake word is armed again only when the session ends.
