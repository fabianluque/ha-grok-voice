"""Serve the browser voice UI over the same ports as the voice WebSocket."""

from __future__ import annotations

import email.utils
import http
from pathlib import Path
from urllib.parse import unquote, urlparse

from websockets.datastructures import Headers
from websockets.http11 import Request, Response

WWW_ROOT = Path(__file__).resolve().parent.parent / "www"

TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}

FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Grok Voice</title>
  </head>
  <body>
    <p>Grok Voice UI is missing from this add-on image. Rebuild the add-on after <code>npm run build</code> in <code>client/</code>.</p>
  </body>
</html>
"""


def is_websocket_upgrade(request: Request) -> bool:
    upgrade = request.headers.get("Upgrade") if request.headers is not None else None
    return upgrade is not None and str(upgrade).lower() == "websocket"


def content_type_for(path: Path) -> str:
    return TYPES.get(path.suffix.lower(), "application/octet-stream")


def resolve_static_path(url_path: str, www_root: Path) -> Path | None:
    parsed = urlparse(url_path)
    relative = unquote(parsed.path).lstrip("/")
    if relative == "" or relative.endswith("/"):
        relative = f"{relative}index.html" if relative else "index.html"
    root = www_root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if target.is_file():
        return target
    return None


def build_response(status: int, body: bytes, content_type: str) -> Response:
    phrase = http.HTTPStatus(status).phrase
    header_items: list[tuple[str, str]] = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
        ("X-Content-Type-Options", "nosniff"),
    ]
    if content_type.startswith("text/html"):
        header_items.append(("Cache-Control", "no-store"))
    return Response(status, phrase, Headers(header_items), body)


def http_file_response(url_path: str, www_root: Path | None = None) -> Response:
    root = www_root or WWW_ROOT
    target = resolve_static_path(url_path, root)
    if target is not None:
        return build_response(200, target.read_bytes(), content_type_for(target))
    relative = unquote(urlparse(url_path).path).lstrip("/")
    wants_index = relative == "" or relative.endswith("/") or relative == "index.html"
    if wants_index:
        return build_response(200, FALLBACK_HTML.encode("utf-8"), "text/html; charset=utf-8")
    return build_response(404, b"Not found\n", "text/plain; charset=utf-8")


def process_http_request(request: Request, www_root: Path | None = None) -> Response | None:
    if is_websocket_upgrade(request):
        return None
    return http_file_response(request.path, www_root)
