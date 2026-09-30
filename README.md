# Grok Voice Agent

A Home Assistant add-on for **full-duplex** voice with [xAI Grok Voice](https://docs.x.ai/). Speak naturally from a browser or a Kiosk Satellite tablet: Grok hears you while it talks, runs Assist tools (lights, music, lists, and more) through your local Home Assistant, and hangs up when you are done.

The xAI API key stays in the add-on. It is never sent to the tablet or pasted into the browser UI.

## What you need

- **Home Assistant OS** or **Supervised** (this is an add-on, not a standalone integration)
- An [xAI API key](https://console.x.ai/) with access to Grok Voice (`grok-voice-think-fast-2.0`)
- Optional: **Kiosk Satellite** if you want wake-word tablets
- Optional: [Music Assistant](https://www.music-assistant.io/), todo / shopping lists, and Mealie if you want those tools on a blank allowlist

The GitHub repository must be **public**. The add-on store does not log into GitHub.

## Install

1. In Home Assistant: **Settings → Add-ons → Add-on store → ⋮ → Repositories**.
2. Add `https://github.com/fabianluque/ha-grok-voice` and save.
3. Open **Grok Voice Agent** and install it. Home Assistant builds the image from this repo.
4. Open the add-on **Configuration** tab and save your options (below).
5. Install the official **Model Context Protocol Server** integration and expose the entities Assist may control.
6. Start the add-on.

Leave **Home Assistant MCP URL** and **Long-lived token** blank. The add-on uses `http://supervisor/core/api/mcp` with the Supervisor token. Paste a long-lived token only if the log shows MCP HTTP 401. Startup should log `mcp_auth=supervisor`.

## Configure

| Option | What to set |
| --- | --- |
| **xAI API key** | Required. Create one at [console.x.ai](https://console.x.ai/). |
| **Home location** | Optional city, region, or ZIP (for example `Austin, TX`). Injected into every session so Grok can talk about weather and nearby events. **Leave blank** until you fill it in. |
| **Default area** | Optional Home Assistant area name used when a kiosk did not send a room (Open Web UI, or lookup failed). Leave blank unless you want a fallback room. |
| **Instructions** | Spoken persona. The shipped default is a short house voice: hang up after a successful home command or a goodbye; after a question it answers and then listens. Edit freely; do not put secrets here. |
| **Idle timeout** | Seconds of silence after Grok finishes before the session ends (default **30**). |

A blank **MCP tool allowlist** attaches lights, live context, media / Music Assistant, todo lists, and Mealie. Set `*` to offer every MCP tool. Details are in [`grok_voice_agent/DOCS.md`](grok_voice_agent/DOCS.md).

Secrets belong only in add-on options (`xai_api_key`, optional long-lived token). Do not put keys in the dashboard inject, git, or `.env` files.

## Try it (Open Web UI)

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

If `homeassistant.local` does not resolve on that tablet, use your Home Assistant LAN IP instead (`http://<HA-LAN>:8080/grok-voice.js`), or set `window.GROK_VOICE_SCRIPT` / `window.GROK_VOICE_URL` before the tag.

3. Reload the kiosk. Later add-on updates refresh `grok-voice.js`; you do not paste the full client into Remote Admin again.

Each kiosk session is scoped to **that tablet’s Home Assistant area**, so “turn on the lights” or “play music” targets this room. Override with `window.GROK_VOICE_AREA` / `GROK_VOICE_AREA_ID` only if lookup is wrong.

Setup, CORS, overlay, and area resolution are in [DOCS.md — Kiosk Satellite](grok_voice_agent/DOCS.md#kiosk-satellite).

## Hang-up and duplex

The microphone stays open while Grok is speaking, so you can talk over a reply (barge-in). The session **hangs up** after:

- a successful **home device or in-home media** action (lights, garage, locks, climate, covers, play/pause/volume),
- **thank you** / **goodbye** / **that’s all** (and similar closers),
- a tap on the kiosk conversation overlay,
- or **idle** silence once Grok has finished (default 30 seconds).

After a question, Grok answers and then **listens quietly** for another utterance. It does not hang up just because it finished a Q&A turn, and you should not prompt it to ask “anything else?”

## Troubleshooting

| Symptom | Where to look |
| --- | --- |
| Add-on does not appear in the store | Repo must be public; you added the GitHub URL under Repositories. |
| Mic blocked in the browser | Use Open Web UI over HTTPS, or see [LAN debug notes](grok_voice_agent/DOCS.md#lan-debug-port-8080). |
| MCP HTTP 401 | Startup log should say `mcp_auth=supervisor`. Paste a long-lived token only as a fallback. |
| Kiosk never starts Grok | Wake word must stay enabled; inject must load `http://<HA-LAN>:8080/grok-voice.js`; tablet must reach port 8080. |
| Lights/music hit the wrong room | Set **Default area**, or `GROK_VOICE_AREA` on that kiosk. Confirm the tablet’s HA area. |
| Grok does not know your city | Fill in **Home location**; leave it blank on purpose if you do not want a city. |

Logs: add-on **Log** tab. Deeper behavior, MCP allowlist, and development notes: [`grok_voice_agent/DOCS.md`](grok_voice_agent/DOCS.md).

## For maintainers

The version Home Assistant reads is `version` in `grok_voice_agent/config.yaml`. Git tags use the same number with a `v` prefix (`1.2.3` → `v1.2.3`).

After this workflow is on `main`, ship from GitHub. The workflow uses the built-in `GITHUB_TOKEN` only. Do not create an API key, and do not push the tag yourself.

1. Open **Actions → Release → Run workflow**.
2. Select branch **main**.
3. Choose a bump:
   - **current** — publish the version already in `grok_voice_agent/config.yaml`. Use this for the first release: nothing is tagged yet.
   - **patch**, **minor**, or **major** — write the next version into `config.yaml`, commit it on `main`, then publish.
4. Wait until the workflow is green. It will run pytest and the client tests, commit `config.yaml` / `CHANGELOG.md` when they changed, build amd64 and aarch64 images (`ghcr.io/fabianluque/grok-voice-agent`), push git tag `vX.Y.Z`, and create a GitHub Release.

Put `Fixes #N` (or `Closes #N`) in a pull request description so GitHub closes that issue on merge.

If **Release** cannot push to `main`, set **Settings → Actions → General → Workflow permissions** to read and write. Reload the add-on store after a publish so Supervisor sees the new `config.yaml` version.
