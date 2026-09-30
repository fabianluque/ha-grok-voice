# Grok Voice Agent

[![CI](https://github.com/fabianluque/ha-grok-voice/actions/workflows/ci.yml/badge.svg)](https://github.com/fabianluque/ha-grok-voice/actions/workflows/ci.yml)
[![Release](https://github.com/fabianluque/ha-grok-voice/actions/workflows/release.yml/badge.svg)](https://github.com/fabianluque/ha-grok-voice/actions/workflows/release.yml)
[![Latest release](https://img.shields.io/github/v/release/fabianluque/ha-grok-voice)](https://github.com/fabianluque/ha-grok-voice/releases/latest)

A Home Assistant add-on for **full-duplex** voice with [xAI Grok Voice](https://docs.x.ai/). Speak naturally from a browser or a Kiosk Satellite tablet. Grok hears you while it talks, runs Assist tools on your local Home Assistant, and hangs up when you are done.

Your xAI API key stays in the add-on. It is never sent to the tablet or pasted into the browser UI.

## What it is

Grok Voice Agent is an **add-on**, not a standalone integration. Install it from this GitHub repository in the Home Assistant Add-on store. After it starts, you can:

- Talk from **Open Web UI** (the add-on page or the **Grok Voice** sidebar)
- Wake a **Kiosk Satellite** tablet and talk in that room
- Control lights, music, lists, and more through Home Assistant’s local MCP server

## Requirements

- **Home Assistant OS** or **Supervised** (add-ons are not available on Container or Core)
- An [xAI API key](https://console.x.ai/) with Grok Voice (`grok-voice-think-fast-2.0`)
- The official **Model Context Protocol Server** integration, with the entities Assist may control
- Optional: **Kiosk Satellite** if you want wake-word tablets
- Optional: [Music Assistant](https://www.music-assistant.io/), todo / shopping lists, and Mealie if you want those tools on a blank allowlist

## Install the repository

1. In Home Assistant, open **Settings → Add-ons → Add-on store**.
2. Open the **⋮** menu and choose **Repositories**.
3. Add `https://github.com/fabianluque/ha-grok-voice` and save.
4. Find **Grok Voice Agent** on the store page and install it. Home Assistant builds the image from this repo (the first install can take a few minutes).
5. Open the add-on **Configuration** tab and save your options (next section).
6. Install **Model Context Protocol Server** if you have not already, and expose the entities Assist may control.
7. Start the add-on.

Leave **Home Assistant MCP URL** and **Long-lived token** blank. The add-on uses `http://supervisor/core/api/mcp` with the Supervisor token. Paste a long-lived token only if the log shows MCP HTTP 401. Startup should log `mcp_auth=supervisor`.

## Configure

You only need the API key to talk. Everything else is optional.

| Option | What to set |
| --- | --- |
| **xAI API key** | Required. Create one at [console.x.ai](https://console.x.ai/). |
| **Home location** | Optional city, region, or ZIP (for example `Austin, TX`). Injected into every session so Grok can talk about weather and nearby events. Leave blank until you fill it in. |
| **Default area** | Optional Home Assistant area name used when a kiosk did not send a room (Open Web UI, or lookup failed). Leave blank unless you want a fallback room. |
| **Duplex LAN host** | Optional Home Assistant LAN IP for Kiosk Satellite tablets that cannot resolve `homeassistant.local` (for example `192.168.86.38`). Leave blank to keep automatic discovery. |
| **Instructions** | Spoken persona. The shipped default hangs up after a successful home command or a goodbye. After a question it answers, may offer more once, then waits. Edit freely. Do not put secrets here. |
| **Idle timeout** | Seconds of silence after Grok finishes before the session ends (default **30**). |

A blank **MCP tool allowlist** attaches lights, live context, media / Music Assistant, todo lists, and Mealie. Set `*` to offer every MCP tool. Details are in [`grok_voice_agent/DOCS.md`](grok_voice_agent/DOCS.md).

Secrets belong only in add-on options (`xai_api_key`, optional long-lived token). Do not put keys in the dashboard inject, git, or `.env` files.

## Open Web UI

The fastest first-run path:

1. Stay signed in to Home Assistant.
2. Open the add-on and choose **Open Web UI**, or use **Grok Voice** in the sidebar.
3. Click **Start talking** and allow the microphone.

The mic UI needs a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts) (HTTPS or `localhost`). Prefer **Open Web UI** when Home Assistant itself is HTTPS (Nabu Casa or a local certificate). Do not bookmark a raw `/api/hassio_ingress/...` URL; that path has no session cookie.

More LAN / Mac-browser paths are in [DOCS.md](grok_voice_agent/DOCS.md#test-from-a-mac-browser).

## Kiosk Satellite (optional)

On a tablet that already runs Kiosk Satellite with wake word enabled:

1. Start **Grok Voice Agent** so port `8080` is listening on the Home Assistant host.
2. In Kiosk Satellite **Remote Admin**, set **Inject JavaScript on the HA dashboard** to a one-line loader that fetches the client from the add-on:

```javascript
(() => {
  const s = document.createElement("script");
  s.src = "http://homeassistant.local:8080/grok-voice.js";
  document.documentElement.appendChild(s);
})();
```

If `homeassistant.local` does not resolve on that tablet, set add-on **Duplex LAN host** (`duplex_lan_host`) to the Home Assistant LAN IP (`192.168.86.38`) and use that IP in the script URL (`http://192.168.86.38:8080/grok-voice.js`). You can still set `window.GROK_VOICE_SCRIPT` / `window.GROK_VOICE_URL` before the tag.

3. Reload the kiosk. Later add-on updates refresh `grok-voice.js`. You do not paste the full client into Remote Admin again.

Each kiosk session is scoped to **that tablet’s Home Assistant area**, so “turn on the lights” or “play music” targets this room. Override with `window.GROK_VOICE_AREA` / `GROK_VOICE_AREA_ID` only if lookup is wrong.

Setup, CORS, overlay, and area resolution are in [DOCS.md — Kiosk Satellite](grok_voice_agent/DOCS.md#kiosk-satellite).

## Hang-up

The microphone stays open while Grok is speaking, so you can talk over a reply (barge-in).

The session **hangs up** after:

- a successful **home device or in-home media** action (lights, garage, locks, climate, covers, play/pause/volume)
- **thank you** / **goodbye** / **that’s all** (and similar closers)
- a tap on the kiosk conversation overlay
- **idle** silence once Grok has finished (default 30 seconds)

Questions stay open. After sports, news, trivia, or other Q&A, Grok gives a **short first answer**, may ask **one** brief offer of more (“Want his term?”), then **stops and waits**. It must not keep talking and answer that offer itself. It does not hang up just because it finished a Q&A turn.

## Troubleshooting

| Symptom | What to try |
| --- | --- |
| Store page is empty after you add the repository | Refresh the Add-on store. Confirm the URL is `https://github.com/fabianluque/ha-grok-voice`. |
| Mic blocked in the browser | Use Open Web UI over HTTPS, or see [LAN debug notes](grok_voice_agent/DOCS.md#lan-debug-port-8080). |
| MCP HTTP 401 | Startup log should say `mcp_auth=supervisor`. Paste a long-lived token only as a fallback. |
| Kiosk never starts Grok | Wake word must stay enabled. The inject must load `http://<HA-LAN>:8080/grok-voice.js`. The tablet must reach port 8080. If the tablet log shows `homeassistant.local` / `ERR_NAME_NOT_RESOLVED`, set **Duplex LAN host** to the HA LAN IP. |
| Lights/music hit the wrong room | Confirm that kiosk’s Home Assistant device has an area. `default_area` / `GROK_VOICE_AREA` only if lookup is still wrong. |
| Grok does not know your city | Fill in **Home location**. Leave it blank on purpose if you do not want a city. |

Logs: add-on **Log** tab. Deeper behavior, MCP allowlist, and development notes: [`grok_voice_agent/DOCS.md`](grok_voice_agent/DOCS.md).

## For maintainers

The version Home Assistant reads is `version` in `grok_voice_agent/config.yaml`. Git tags use the same number with a `v` prefix (`1.2.3` → `v1.2.3`).

Ship from GitHub. The workflow uses the built-in `GITHUB_TOKEN` only. Do not create an API key, and do not push the tag yourself.

1. Open **Actions → Release → Run workflow**.
2. Select branch **main**.
3. Choose a bump:
   - **current** — publish the version already in `grok_voice_agent/config.yaml`. Use this for the first release: nothing is tagged yet.
   - **patch**, **minor**, or **major** — write the next version into `config.yaml`, commit it on `main`, then publish.
4. Wait until the workflow is green. It will run pytest and the client tests, commit `config.yaml` / `CHANGELOG.md` when they changed, build amd64 and aarch64 images (`ghcr.io/fabianluque/grok-voice-agent`), push git tag `vX.Y.Z`, and create a GitHub Release.

Put `Fixes #N` (or `Closes #N`) in a pull request description so GitHub closes that issue on merge.

If **Release** cannot push to `main`, set **Settings → Actions → General → Workflow permissions** to read and write. Reload the add-on store after a publish so Supervisor sees the new `config.yaml` version.
