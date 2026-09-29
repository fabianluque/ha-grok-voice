# Grok Voice Agent

Home Assistant add-on for [Grok Voice](https://x.ai): talk to **Grok Voice Think Fast 2.0** from a Mac browser, a tablet, or a Kiosk Satellite dashboard. Assist tools run through the local Home Assistant MCP server. The xAI API key stays in the add-on.

## Install from the add-on store

The GitHub repository must be **public** so Home Assistant can clone it.

1. In Home Assistant: **Settings → Add-ons → Add-on store → ⋮ → Repositories**.
2. Add [`https://github.com/fabianluque/ha-grok-voice`](https://github.com/fabianluque/ha-grok-voice) and save.
3. Install **Grok Voice Agent**.
4. Open **Configuration**, set **xAI API key**, save. Do not put that key in the browser.
5. Install the official **Model Context Protocol Server** integration and expose the entities Assist may control.
6. Start the add-on.

Then open **Open Web UI** / the **Grok Voice** sidebar panel (or the debug port) and click **Start talking**.

Full setup, MCP notes, Mac/tablet testing, Kiosk Satellite inject, and local `/addons` development: [`grok_voice_agent/DOCS.md`](grok_voice_agent/DOCS.md).

## Cut a release

Supervisor installs from git (`config.yaml` `version` on `main`), not from GHCR. Tags still build images and a GitHub Release.

1. Bump `version` in `grok_voice_agent/config.yaml` (quoted string, for example `"0.3.0"`).
2. If the browser UI changed, run `npm test && npm run build` in `client/` and commit `grok_voice_agent/www`.
3. Merge that commit to `main`.
4. Tag the same commit and push it:

   ```bash
   git tag v0.3.0
   git push origin v0.3.0
   ```

   The tag must match `config.yaml` (`v0.3.0` ↔ `0.3.0`) or the Release workflow fails.

5. GitHub Actions lints, tests, builds `amd64` and `aarch64` images, publishes

   `ghcr.io/fabianluque/ha-grok-voice/grok-voice-agent:0.3.0`

   and opens a GitHub Release.
6. In Home Assistant: add-on store → ⋮ on this repository → reload / check for updates. Supervisor sees the new `version` on `main` and offers **Update**.
