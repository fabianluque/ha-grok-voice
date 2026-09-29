#!/usr/bin/env python3
"""Version and changelog helpers for the Release workflow.

The add-on version Home Assistant installs is ``grok_voice_agent/config.yaml``.
This script bumps that semver, prepends CHANGELOG.md, and decides whether a
workflow run should publish. It uses only the standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VERSION_LINE = re.compile(
    r'^(version:[ \t]*)(["\']?)(\d+\.\d+\.\d+)\2[ \t]*$',
    re.MULTILINE,
)
BUMPS = ("current", "patch", "minor", "major")
CHANGELOG_INTRO = (
    "# Changelog\n"
    "\n"
    "Versions match `grok_voice_agent/config.yaml`. GitHub Releases list the\n"
    "merged pull requests since the previous tag. Issues closed with `Fixes #N`\n"
    "in those pull requests are included below when GitHub reports them.\n"
)


@dataclass(frozen=True)
class Decision:
    release: bool
    version: str
    reason: str


def read_version(text: str) -> str:
    match = VERSION_LINE.search(text)
    if not match:
        raise SystemExit("config.yaml has no version: X.Y.Z line")
    return match.group(3)


def bump_version(version: str, bump: str) -> str:
    if bump not in BUMPS:
        raise SystemExit(f"unknown bump: {bump}")
    major, minor, patch = (int(part) for part in version.split("."))
    if bump == "current":
        return version
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major + 1}.0.0"


def set_version_text(text: str, version: str) -> str:
    match = VERSION_LINE.search(text)
    if not match:
        raise SystemExit("config.yaml has no version: X.Y.Z line")
    quote = match.group(2)
    replacement = f"{match.group(1)}{quote}{version}{quote}"
    return text[: match.start()] + replacement + text[match.end() :]


def build_from(text: str, arch: str) -> str:
    pattern = re.compile(
        rf"^[ \t]*{re.escape(arch)}:[ \t]*[\"']?([^\"'#\n]+?)[\"']?[ \t]*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"build.yaml has no build_from image for {arch}")
    image = match.group(1).strip()
    if not image:
        raise SystemExit(f"build.yaml build_from for {arch} is empty")
    return image


def has_changelog_heading(existing: str, version: str) -> bool:
    return (
        re.search(rf"^## \[{re.escape(version)}\](?:\s|$)", existing, re.MULTILINE)
        is not None
    )


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def filter_closed_issues(issues: list[dict], since: str | None) -> list[dict]:
    since_dt = parse_time(since) if since else None
    chosen: list[dict] = []
    for issue in issues:
        closed = issue.get("closedAt")
        if not closed:
            continue
        if since_dt is not None and parse_time(str(closed)) < since_dt:
            continue
        number = issue.get("number")
        title = str(issue.get("title", "")).strip()
        if not isinstance(number, int) or not title:
            continue
        chosen.append({"number": number, "title": title})
    chosen.sort(key=lambda item: item["number"])
    return chosen


def format_closed_issues(issues: list[dict]) -> str:
    if not issues:
        return ""
    lines = ["### Closed issues", ""]
    lines.extend(f"- #{issue['number']} {issue['title']}" for issue in issues)
    return "\n".join(lines) + "\n"


def prepend_changelog(existing: str, version: str, day: str, notes: str) -> str:
    if has_changelog_heading(existing, version):
        return existing
    body = notes.strip() or "No merged pull requests since the previous tag."
    section = f"## [{version}] - {day}\n\n{body}\n"
    if not existing.strip():
        return CHANGELOG_INTRO + "\n" + section
    match = re.search(r"^## ", existing, re.MULTILINE)
    if not match:
        trailer = existing if existing.endswith("\n") else existing + "\n"
        return trailer + "\n" + section
    return existing[: match.start()] + section + "\n" + existing[match.start() :]


def load_tags(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip()}


def decide(
    *,
    event: str,
    bump: str,
    current: str,
    parent: str | None,
    tags: set[str],
) -> Decision:
    if event == "push":
        if parent is not None and parent == current:
            return Decision(False, current, "config.yaml version did not change")
        target = current
        if f"v{target}" in tags:
            return Decision(False, target, f"v{target} already exists")
        return Decision(True, target, "version on main has no tag yet")

    if event != "workflow_dispatch":
        raise SystemExit(f"unsupported event: {event}")
    if bump not in BUMPS:
        raise SystemExit(f"unknown bump: {bump}")
    target = bump_version(current, bump)
    if f"v{target}" in tags:
        if bump == "current":
            return Decision(False, target, f"v{target} already exists")
        raise SystemExit(
            f"v{target} already exists. Publish a version that does not have a tag yet."
        )
    return Decision(True, target, f"{bump} release")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"file not found: {path}") from exc


def _command_version(args: argparse.Namespace) -> None:
    print(read_version(_read(Path(args.config))))


def _command_set_version(args: argparse.Namespace) -> None:
    path = Path(args.config)
    path.write_text(set_version_text(_read(path), args.version), encoding="utf-8")


def _command_build_from(args: argparse.Namespace) -> None:
    print(build_from(_read(Path(args.build)), args.arch))


def _command_decide(args: argparse.Namespace) -> None:
    parent = None
    if args.parent_config:
        parent = read_version(_read(Path(args.parent_config)))
    tags = load_tags(_read(Path(args.tags_file))) if args.tags_file else set()
    decision = decide(
        event=args.event,
        bump=args.bump,
        current=read_version(_read(Path(args.config))),
        parent=parent,
        tags=tags,
    )
    print("true" if decision.release else "false")
    print(decision.version)
    print(decision.reason)


def _load_issues(path: str | None) -> list[dict]:
    if not path:
        return []
    payload = json.loads(_read(Path(path)))
    if not isinstance(payload, list):
        raise SystemExit("issues file must be a JSON list")
    return payload


def _command_changelog(args: argparse.Namespace) -> None:
    notes = _read(Path(args.notes)).strip()
    issues = format_closed_issues(
        filter_closed_issues(_load_issues(args.issues), args.since or None)
    ).strip()
    if issues:
        notes = f"{notes}\n\n{issues}" if notes else issues
    path = Path(args.changelog)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        prepend_changelog(existing, args.version, args.date, notes),
        encoding="utf-8",
    )


def _command_format_issues(args: argparse.Namespace) -> None:
    section = format_closed_issues(
        filter_closed_issues(_load_issues(args.issues), args.since or None)
    )
    if args.output:
        Path(args.output).write_text(section, encoding="utf-8")
    count = 0 if not section else section.count("\n- #")
    print(count)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    version = sub.add_parser("version")
    version.add_argument("--config", required=True)
    version.set_defaults(func=_command_version)

    set_version = sub.add_parser("set-version")
    set_version.add_argument("--config", required=True)
    set_version.add_argument("--version", required=True)
    set_version.set_defaults(func=_command_set_version)

    base = sub.add_parser("build-from")
    base.add_argument("--build", required=True)
    base.add_argument("--arch", required=True)
    base.set_defaults(func=_command_build_from)

    decision = sub.add_parser("decide")
    decision.add_argument("--event", required=True)
    decision.add_argument("--bump", default="current")
    decision.add_argument("--config", required=True)
    decision.add_argument("--parent-config")
    decision.add_argument("--tags-file")
    decision.set_defaults(func=_command_decide)

    changelog = sub.add_parser("changelog")
    changelog.add_argument("--version", required=True)
    changelog.add_argument("--notes", required=True)
    changelog.add_argument("--date", required=True)
    changelog.add_argument("--changelog", required=True)
    changelog.add_argument("--issues")
    changelog.add_argument("--since", default="")
    changelog.set_defaults(func=_command_changelog)

    issues = sub.add_parser("format-issues")
    issues.add_argument("--issues", required=True)
    issues.add_argument("--since", default="")
    issues.add_argument("--output")
    issues.set_defaults(func=_command_format_issues)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
