"""Serve the browser voice UI over the same ports as the voice WebSocket."""

from __future__ import annotations

import email.utils
import hashlib
import http
from pathlib import Path
from urllib.parse import unquote, urlparse

from websockets.datastructures import Headers
from websockets.http11 import Request, Response

WWW_ROOT = Path(__file__).resolve().parent.parent / "www"

# Packaged kiosk IIFE. Lovelace dashboards load this from the debug port
# (``http://<HA-LAN>:8080/grok-voice.js``). That GET is unauthenticated on
# purpose: a classic ``<script src>`` cannot send a Bearer token, and the
# dashboard has no Supervisor ingress_session cookie. Duplex still authenticates
# on the WebSocket. ``/api/hassio_ingress/<token>/grok-voice.js`` 401s from
# Lovelace; Open Web UI / port 8099 can serve the same path if you are already
# in an ingress session.
KIOSK_CLIENT_PATH = "/grok-voice.js"

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


def etag_for(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:16] + '"'


def if_none_match_hits(header: str | None, etag: str) -> bool:
    if not header:
        return False
    needle = etag.strip().lower()
    for part in header.split(","):
        candidate = part.strip().lower()
        if candidate.startswith("w/"):
            candidate = candidate[2:].strip()
        if candidate == needle:
            return True
    return False


def extra_static_headers(content_type: str) -> list[tuple[str, str]]:
    """Cache and CORS rules for packaged files.

    Classic ``<script src>`` from a Lovelace / Kiosk Satellite page is
    cross-origin to ``:8080`` and does not need CORS. CORS is still sent on JS
    so a dashboard resource or ``fetch`` can load the same file. ``no-cache``
    forces the tablet to revalidate after an add-on update instead of keeping a
    stale grok-voice.js.
    """
    headers: list[tuple[str, str]] = []
    if content_type.startswith("text/html"):
        headers.append(("Cache-Control", "no-store"))
        return headers
    if "javascript" in content_type:
        headers.extend(
            (
                ("Cache-Control", "no-cache"),
                ("Access-Control-Allow-Origin", "*"),
                ("Cross-Origin-Resource-Policy", "cross-origin"),
            )
        )
    return headers


def build_response(
    status: int,
    body: bytes,
    content_type: str,
    extra: list[tuple[str, str]] | None = None,
) -> Response:
    phrase = http.HTTPStatus(status).phrase
    header_items: list[tuple[str, str]] = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
        ("X-Content-Type-Options", "nosniff"),
    ]
    header_items.extend(extra_static_headers(content_type))
    if extra:
        header_items.extend(extra)
    return Response(status, phrase, Headers(header_items), body)


def http_file_response(
    url_path: str,
    www_root: Path | None = None,
    request_headers: Headers | None = None,
) -> Response:
    root = www_root or WWW_ROOT
    target = resolve_static_path(url_path, root)
    if target is not None:
        body = target.read_bytes()
        content_type = content_type_for(target)
        etag = etag_for(body)
        extra = [("ETag", etag)]
        match_header = None
        if request_headers is not None:
            match_header = request_headers.get("If-None-Match")
        if if_none_match_hits(match_header, etag):
            return build_response(304, b"", content_type, extra)
        return build_response(200, body, content_type, extra)
    relative = unquote(urlparse(url_path).path).lstrip("/")
    wants_index = relative == "" or relative.endswith("/") or relative == "index.html"
    if wants_index:
        return build_response(200, FALLBACK_HTML.encode("utf-8"), "text/html; charset=utf-8")
    return build_response(404, b"Not found\n", "text/plain; charset=utf-8")


def process_http_request(request: Request, www_root: Path | None = None) -> Response | None:
    if is_websocket_upgrade(request):
        return None
    return http_file_response(request.path, www_root, request.headers)
