"""GitHub add-on store layout: repository.yaml plus an add-on folder."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "grok_voice_agent"


def test_repository_yaml_identifies_this_github_repo():
    text = (ROOT / "repository.yaml").read_text(encoding="utf-8")
    assert "name:" in text
    assert "https://github.com/fabianluque/ha-grok-voice" in text
    assert "maintainer:" in text


def test_store_discovers_grok_voice_agent_from_the_repo_root():
    config = (ADDON / "config.yaml").read_text(encoding="utf-8")
    assert "slug: grok_voice_agent" in config
    assert "name: Grok Voice Agent" in config
    assert (ADDON / "Dockerfile").is_file()
    assert (ADDON / "build.yaml").is_file()
    assert "COPY www /app/www" in (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert "Grok Voice" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "xAI" in (ADDON / "README.md").read_text(encoding="utf-8")


def test_github_actions_lint_test_and_release_the_addon():
    ci = (ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yaml").read_text(encoding="utf-8")
    assert "frenck/action-addon-linter" in ci
    assert "path: ./grok_voice_agent" in ci
    assert "python -m pytest" in ci
    assert "npm test" in ci
    assert "grok_voice_agent/Dockerfile" in ci
    assert 'tags:\n      - "v*.*.*"' in release
    assert "config.yaml version" in release
    assert "action-gh-release" in release
    translations = (ADDON / "translations/en.yaml").read_text(encoding="utf-8")
    assert "8080/tcp" in translations
