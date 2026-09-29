# Grok Voice

Home Assistant add-on for full-duplex Grok Voice. The add-on folder is `grok_voice_agent/`. Install and day-to-day use are in [`grok_voice_agent/DOCS.md`](grok_voice_agent/DOCS.md).

The version Home Assistant reads is `version` in `grok_voice_agent/config.yaml`. Git tags use the same number with a `v` prefix (`1.2.3` → `v1.2.3`).

## Cut a release

After this workflow is on `main`, ship from GitHub. The workflow uses the built-in `GITHUB_TOKEN` only. Do not create an API key, and do not push the tag yourself.

1. Open **Actions → Release → Run workflow**.
2. Select branch **main**.
3. Choose a bump:
   - **current** — publish the version already in `grok_voice_agent/config.yaml`. Use this for the first release: nothing is tagged yet.
   - **patch**, **minor**, or **major** — write the next version into `config.yaml`, commit it on `main`, then publish.
4. Wait until the workflow is green. It will:
   - Run pytest and the client tests.
   - Commit `config.yaml` and a `CHANGELOG.md` section when they changed (`Release vX.Y.Z`).
   - Build amd64 and aarch64 images and push:
     - `ghcr.io/fabianluque/grok-voice-agent:X.Y.Z`
     - `ghcr.io/fabianluque/grok-voice-agent:latest`
     - `ghcr.io/fabianluque/amd64-grok-voice-agent` and `ghcr.io/fabianluque/aarch64-grok-voice-agent` (the per-arch images behind that manifest)
   - Push git tag `vX.Y.Z` (it must match `config.yaml`).
   - Create a GitHub Release named `vX.Y.Z`. The notes are GitHub's generated list of merged pull requests since the previous tag. Closed issues since that tag are prepended when GitHub returns any.

### Version bump in a pull request

Change `version` in `grok_voice_agent/config.yaml` and merge to `main`. That push publishes the new version the same way as **current**. It does not bump a second time. Leave the tag to the workflow.

### Close an issue from a pull request

Put `Fixes #N` (or `Closes #N`) in the pull request description. GitHub closes the issue when the pull request merges. The following release lists that pull request, and lists the issue when it was closed after the previous tag.

### If the image build fails

A **patch** / **minor** / **major** run commits the version before it builds images. The tag is created only after the images exist. Run **Release** again and choose **current**. That retries the build for the version already on `main` and then tags it.

### After it is green

Reload the add-on store in Home Assistant so Supervisor sees the new `config.yaml` version. Store installs still build from this git repo. `config.yaml` does not set `image:` yet, so Supervisor will not try to pull GHCR before the first publish exists.

The first GHCR push creates the container package. In the package settings, set the visibility to public if other machines should pull `ghcr.io/fabianluque/grok-voice-agent`.

If **Release** cannot push to `main`, set **Settings → Actions → General → Workflow permissions** to read and write.
