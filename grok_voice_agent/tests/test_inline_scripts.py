"""Served HTML inline JavaScript must parse; the 0.3.1 panel shipped a syntax error.

Run as a script (CI) or via pytest. Both extract every inline ``<script>``
from served HTML and run ``node --check``.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HTML_GLOBS = (
    "grok_voice_agent/**/*.html",
    "client/**/*.html",
)
SKIP_PARTS = ("node_modules", "dist", ".git")
SCRIPT_RE = re.compile(r"<script(\s[^>]*)?>(.*?)</script>", re.IGNORECASE | re.DOTALL)
SRC_RE = re.compile(r"\bsrc\s*=", re.IGNORECASE)


def html_files() -> list[Path]:
    found: list[Path] = []
    for pattern in HTML_GLOBS:
        for path in REPO.glob(pattern):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.is_file():
                found.append(path)
    return sorted(found)


def inline_scripts(html: str) -> list[str]:
    bodies: list[str] = []
    for attrs, body in SCRIPT_RE.findall(html):
        if SRC_RE.search(attrs or ""):
            continue
        text = body.strip()
        if text:
            bodies.append(text)
    return bodies


def node_check(source: str, *, node: str = "node") -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(source)
        path = handle.name
    try:
        return subprocess.run(
            [node, "--check", path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)


def check_inline_scripts() -> list[str]:
    """Return human-readable failures (empty means all inline scripts parsed)."""
    failures: list[str] = []
    scanned = 0
    for path in html_files():
        scripts = inline_scripts(path.read_text(encoding="utf-8"))
        for index, source in enumerate(scripts, start=1):
            scanned += 1
            result = node_check(source)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "node --check failed").strip()
                rel = path.relative_to(REPO)
                failures.append(f"{rel} inline script #{index}: {detail}")
    if scanned == 0:
        failures.append("no inline scripts found in served HTML")
    return failures


def test_served_inline_scripts_parse_with_node():
    assert check_inline_scripts() == []


def test_panel_html_has_an_inline_script():
    panel = next(
        path for path in html_files() if path.name == "index.html" and "ks_relay" in path.parts
    )
    scripts = inline_scripts(panel.read_text(encoding="utf-8"))
    assert scripts
    assert "function nav(" in scripts[0]
    assert "TABS=" in scripts[0] or "TABS =" in scripts[0]


def test_node_check_rejects_orphaned_else():
    """The 0.3.1 bug: a semicolon after an if-block left ``else`` as a free token."""
    result = node_check("if (true) { x = 1 };\nelse { x = 0; }\n")
    assert result.returncode != 0
    assert "Unexpected token" in (result.stderr or "")


def main() -> int:
    failures = check_inline_scripts()
    if failures:
        print("inline script parse failed:", file=sys.stderr)
        for item in failures:
            print(item, file=sys.stderr)
        return 1
    print("inline scripts ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
