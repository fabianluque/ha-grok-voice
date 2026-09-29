# Grok Voice Agent

Talk to **Grok Voice Think Fast 2.0** from Home Assistant: a mic Web UI in Open Web UI / the sidebar, a debug page on port 8080, or a Kiosk Satellite dashboard. Grok can run Assist tools through the official local MCP server. Your xAI API key never leaves this add-on.

## After install

1. Set **xAI API key** in Configuration and save.
2. Enable the **Model Context Protocol Server** integration and expose the entities Assist should control.
3. Start the add-on.
4. Open **Open Web UI** or the **Grok Voice** panel, allow the microphone, and press **Start talking**.

On a Mac, prefer HA over HTTPS (or an SSH tunnel to `http://127.0.0.1:8080`) so the browser treats the page as a secure context. Paste a long-lived Home Assistant token only on the debug port.

Leave the MCP URL blank unless the log shows HTTP 401. A blank tool allowlist is the Assist control set (`HassTurnOn`, `HassTurnOff`, `HassLightSet`, `GetLiveContext`, `GetDateTime`). Set `*` to offer every MCP tool.

## Links

- Repository: [github.com/fabianluque/ha-grok-voice](https://github.com/fabianluque/ha-grok-voice)
- Detailed docs (install, Mac/tablet testing, Kiosk Satellite, releases): [DOCS.md](DOCS.md)
