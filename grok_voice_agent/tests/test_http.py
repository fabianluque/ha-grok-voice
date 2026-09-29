"""The add-on HTTP ports serve the mic UI instead of a text stub."""

import asyncio
from pathlib import Path

import aiohttp
import httpx
from websockets.asyncio.client import connect
from websockets.datastructures import Headers
from websockets.http11 import Request

from app.server import drop_empty_content_length, voice_serve
from app.static import FALLBACK_HTML, http_file_response, process_http_request


def _request(path: str, upgrade: str | None = None) -> Request:
    headers = Headers()
    if upgrade:
        headers["Upgrade"] = upgrade
        headers["Connection"] = "Upgrade"
    return Request(path, headers)


def test_websocket_upgrade_is_left_to_the_voice_handler():
    assert process_http_request(_request("/", "websocket")) is None
    assert process_http_request(_request("/api/hassio_ingress/token/", "WebSocket")) is None


def test_root_serves_packaged_html(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!DOCTYPE html><title>Grok Voice</title><h1>Start talking</h1>", encoding="utf-8")
    (tmp_path / "ui.js").write_text("window.GrokVoiceUI = true;", encoding="utf-8")

    root = process_http_request(_request("/"), tmp_path)
    indexed = http_file_response("/index.html", tmp_path)
    script = http_file_response("/ui.js", tmp_path)

    assert root is not None
    assert root.status_code == 200
    assert "text/html" in root.headers["Content-Type"]
    assert b"Start talking" in root.body
    assert indexed.body == root.body
    assert script.status_code == 200
    assert "javascript" in script.headers["Content-Type"]
    assert b"GrokVoiceUI" in script.body


def test_missing_ui_falls_back_to_html_not_plaintext():
    response = http_file_response("/", Path("/tmp/grok-voice-missing-www"))
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
    assert b"Grok Voice" in response.body
    assert FALLBACK_HTML.encode("utf-8") == response.body
    assert response.body != b"Grok Voice agent\n"


def test_missing_asset_is_404_when_index_exists(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!DOCTYPE html><title>Grok Voice</title>", encoding="utf-8")
    response = http_file_response("/no-such-file.js", tmp_path)
    assert response.status_code == 404


def test_path_traversal_is_rejected(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!DOCTYPE html><title>Grok Voice</title>", encoding="utf-8")
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    response = http_file_response("/../secret.txt", tmp_path)
    assert response.status_code == 404
    assert b"nope" not in response.body


def test_live_get_returns_html_on_the_voice_port(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!DOCTYPE html><title>Grok Voice</title><button>Start talking</button>",
        encoding="utf-8",
    )

    async def run():
        async def handler(websocket):
            await websocket.wait_closed()

        async def process_request(_connection, request):
            return process_http_request(request, tmp_path)

        async with voice_serve(handler, "127.0.0.1", 0, process_request) as server:
            sockets = list(server.sockets or [])
            port = sockets[0].getsockname()[1]
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://127.0.0.1:{port}/")
                return response.status_code, response.headers.get("content-type"), response.text

    status, content_type, text = asyncio.run(run())
    assert status == 200
    assert content_type is not None and "html" in content_type
    assert "Start talking" in text
    assert "Grok Voice agent" not in text


def test_drop_empty_content_length_keeps_other_headers():
    raw = (
        b"GET /ui.js HTTP/1.1\r\n"
        b"Host: 172.30.32.1:8099\r\n"
        b"X-Ingress-Path: /api/hassio_ingress/token\r\n"
        b"Content-Length: 0\r\n"
        b"Accept: */*\r\n"
    )
    rewritten = drop_empty_content_length(raw)
    assert b"Content-Length" not in rewritten
    assert b"GET /ui.js HTTP/1.1" in rewritten
    assert b"X-Ingress-Path: /api/hassio_ingress/token" in rewritten
    assert b"Accept: */*" in rewritten


def test_nonzero_content_length_is_left_intact():
    raw = b"POST / HTTP/1.1\r\nContent-Length: 4\r\n"
    assert drop_empty_content_length(raw) == raw


def test_ingress_get_with_content_length_zero_returns_the_mic_page(tmp_path: Path):
    """Supervisor's aiohttp proxy sends Content-Length: 0 on the iframe GET."""
    (tmp_path / "index.html").write_text(
        "<!DOCTYPE html><title>Grok Voice</title><button>Start talking</button>",
        encoding="utf-8",
    )
    (tmp_path / "ui.js").write_text("window.GrokVoiceUI = true;", encoding="utf-8")

    async def run():
        async def handler(websocket):
            await websocket.recv()
            await websocket.send("pong")

        async def process_request(_connection, request):
            return process_http_request(request, tmp_path)

        async with voice_serve(handler, "127.0.0.1", 0, process_request) as server:
            port = list(server.sockets or [])[0].getsockname()[1]
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"http://127.0.0.1:{port}/",
                    allow_redirects=False,
                    data=b"",
                    skip_auto_headers={"Content-Type"},
                    headers={
                        "X-Hass-Source": "core.ingress",
                        "X-Ingress-Path": "/api/hassio_ingress/token",
                    },
                ) as page:
                    page_body = await page.read()
                    page_status = page.status
                    page_type = page.headers.get("Content-Type")
                    frame_options = page.headers.get("X-Frame-Options")
                async with session.get(
                    f"http://127.0.0.1:{port}/ui.js",
                    allow_redirects=False,
                    data=b"",
                    skip_auto_headers={"Content-Type"},
                ) as script:
                    script_status = script.status
                    script_type = script.headers.get("Content-Type")
                    script_body = await script.read()
            async with connect(f"ws://127.0.0.1:{port}/") as websocket:
                await websocket.send("ping")
                echoed = await websocket.recv()
            return (
                page_status,
                page_type,
                page_body,
                frame_options,
                script_status,
                script_type,
                script_body,
                echoed,
            )

    status, content_type, body, frame_options, script_status, script_type, script_body, echoed = (
        asyncio.run(run())
    )
    assert status == 200
    assert content_type is not None and content_type.startswith("text/html")
    assert b"<!DOCTYPE html>" in body
    assert b"Start talking" in body
    assert frame_options is None
    assert script_status == 200
    assert script_type is not None and "javascript" in script_type
    assert b"GrokVoiceUI" in script_body
    assert echoed == "pong"


def test_packaged_www_is_the_mic_ui():
    root = Path(__file__).resolve().parents[1] / "www"
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "Grok Voice" in html
    assert "Start talking" in html
    assert "ui.js" in html
    assert (root / "ui.js").is_file()
