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
