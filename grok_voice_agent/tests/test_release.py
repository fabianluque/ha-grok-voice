"""Release workflow decisions, changelog text, and the documented ship path."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "release_version.py"
CONFIG = ROOT / "grok_voice_agent" / "config.yaml"
BUILD = ROOT / "grok_voice_agent" / "build.yaml"


def load_release():
    spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_addon_version_is_tagged_with_a_v_prefix():
    release = load_release()
    version = release.read_version(CONFIG.read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.has_changelog_heading(changelog, version)


def test_bump_and_quoted_version_round_trip():
    release = load_release()
    text = 'name: Demo\nversion: "0.2.1"\nslug: demo\n'
    assert release.read_version(text) == "0.2.1"
    assert release.bump_version("0.2.1", "current") == "0.2.1"
    assert release.bump_version("0.2.1", "patch") == "0.2.2"
    assert release.bump_version("0.2.1", "minor") == "0.3.0"
    assert release.bump_version("0.2.1", "major") == "1.0.0"
    updated = release.set_version_text(text, "0.2.2")
    assert 'version: "0.2.2"' in updated
    assert "name: Demo" in updated
    assert release.read_version(updated) == "0.2.2"


def test_changelog_inserts_once_and_does_not_confuse_longer_versions():
    release = load_release()
    existing = "# Changelog\n\n## [0.2.1] - 2026-09-29\n\n- shipped\n"
    again = release.prepend_changelog(existing, "0.2.1", "2026-09-30", "notes")
    assert again == existing
    added = release.prepend_changelog(existing, "0.2.10", "2026-09-30", "ten")
    assert added.index("## [0.2.10]") < added.index("## [0.2.1]")
    assert "ten" in added


def test_closed_issues_since_previous_tag_only():
    release = load_release()
    issues = [
        {"number": 4, "title": "Older", "closedAt": "2026-09-01T00:00:00Z"},
        {"number": 9, "title": "Shipped", "closedAt": "2026-09-29T12:00:00Z"},
        {"number": 2, "title": "No time"},
    ]
    chosen = release.filter_closed_issues(issues, "2026-09-15T00:00:00Z")
    section = release.format_closed_issues(chosen)
    assert chosen == [{"number": 9, "title": "Shipped"}]
    assert "- #9 Shipped" in section
    assert "Older" not in section
    assert release.format_closed_issues([]) == ""


def test_push_publishes_a_new_version_and_ignores_other_config_edits():
    release = load_release()
    unchanged = release.decide(
        event="push",
        bump="patch",
        current="0.2.2",
        parent="0.2.2",
        tags=set(),
    )
    assert unchanged.release is False

    publish = release.decide(
        event="push",
        bump="current",
        current="0.2.2",
        parent="0.2.1",
        tags={"v0.2.1"},
    )
    assert publish.release is True
    assert publish.version == "0.2.2"

    already = release.decide(
        event="push",
        bump="current",
        current="0.2.2",
        parent="0.2.1",
        tags={"v0.2.2"},
    )
    assert already.release is False


def test_workflow_dispatch_bump_and_current():
    release = load_release()
    current = release.decide(
        event="workflow_dispatch",
        bump="current",
        current="0.2.1",
        parent=None,
        tags=set(),
    )
    assert current.release is True
    assert current.version == "0.2.1"

    already = release.decide(
        event="workflow_dispatch",
        bump="current",
        current="0.2.1",
        parent=None,
        tags={"v0.2.1"},
    )
    assert already.release is False

    patched = release.decide(
        event="workflow_dispatch",
        bump="patch",
        current="0.2.1",
        parent=None,
        tags={"v0.2.1"},
    )
    assert patched.release is True
    assert patched.version == "0.2.2"


def test_build_from_matches_the_addon_arches():
    release = load_release()
    text = BUILD.read_text(encoding="utf-8")
    amd64 = release.build_from(text, "amd64")
    aarch64 = release.build_from(text, "aarch64")
    assert amd64.startswith("ghcr.io/home-assistant/")
    assert "amd64" in amd64
    assert "aarch64" in aarch64


def test_ship_workflows_and_readme():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release_yml = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pytest" in ci
    assert "npm test" in ci
    assert "push: false" in ci
    assert "workflow_dispatch" in release_yml
    assert "generate_release_notes: true" in release_yml
    assert "grok-voice-agent" in release_yml
    assert "home-assistant/builder/actions/build-image@2026.03.2" in release_yml
    assert "softprops/action-gh-release@v3" in release_yml
    assert "Actions → Release → Run workflow" in readme
    assert "**current**" in readme
    assert "**patch**" in readme
    assert "Fixes #N" in readme
    assert "ghcr.io/fabianluque/grok-voice-agent" in readme
    subprocess.run(
        ["bash", "-n", str(ROOT / ".github" / "scripts" / "prepare-release.sh")],
        check=True,
    )
