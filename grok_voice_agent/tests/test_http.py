"""The add-on HTTP ports serve the mic UI instead of a text stub."""

import asyncio
from pathlib import Path

import httpx
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Request

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

        async with serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
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


def test_packaged_www_is_the_mic_ui():
    root = Path(__file__).resolve().parents[1] / "www"
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "Grok Voice" in html
    assert "Start talking" in html
    assert "ui.js" in html
    assert (root / "ui.js").is_file()
