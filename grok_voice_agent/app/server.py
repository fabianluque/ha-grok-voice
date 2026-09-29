"""Browser voice socket. The Grok connection opens only after HA accepts the token."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from websockets.asyncio.client import connect as connect_grok
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.auth import HaAuth, redact
from app.grok_session import GrokBridge, build_session
from app.mcp_client import McpHttpClient, function_tools
from app.tools import ToolGateway

log = logging.getLogger("grok_voice")
GROK_URL = "wss://api.x.ai/v1/realtime"


class VoiceConnection:
    def __init__(self, settings, http, grok_connect=None) -> None:
        self.settings = settings
        self.http = http
        self.grok_connect = grok_connect or _default_grok_connect
        self.grok = None

    async def authenticate(self, token: str) -> bool:
        auth = HaAuth(self.settings.ha_api_url, self.http)
        if not await auth.validate(token):
            return False
        self.grok = await self.grok_connect(self.settings)
        return True


async def _default_grok_connect(settings):
    if not settings.xai_api_key:
        raise RuntimeError("xai_api_key is empty")
    return await connect_grok(
        f"{GROK_URL}?model={settings.model}",
        additional_headers={"Authorization": f"Bearer {settings.xai_api_key}"},
    )


def install_redacting_logs(secrets: list[str]) -> None:
    class _Redact(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = redact(str(record.msg), secrets)
            if record.args:
                record.args = tuple(
                    redact(arg, secrets) if isinstance(arg, str) else arg for arg in record.args
                )
            return True

    logging.getLogger().addFilter(_Redact())


async def handle_socket(websocket, settings, http) -> None:
    connection = VoiceConnection(settings, http)
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=10)
    except (asyncio.TimeoutError, ConnectionClosed):
        return
    if isinstance(raw, bytes):
        await _end(websocket, "unauthorized")
        return
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        await _end(websocket, "unauthorized")
        return
    token = message.get("token") if isinstance(message, dict) else None
    if not isinstance(message, dict) or message.get("type") != "auth":
        log.info("voice auth rejected")
        await _end(websocket, "unauthorized")
        return
    try:
        authorized = await connection.authenticate(str(token or ""))
    except Exception as exc:
        log.error("voice session failed before it was ready: %s", type(exc).__name__)
        await _end(websocket, "error")
        return
    if not authorized:
        log.info("voice auth rejected")
        await _end(websocket, "unauthorized")
        return

    log.info("voice session authenticated")
    gateway = ToolGateway(
        McpHttpClient(settings.ha_mcp_url, settings.mcp_token, http),
        settings.allowlist,
    )
    try:
        listed = await gateway.mcp.list_tools()
    except Exception:
        log.exception("MCP tool list failed")
        listed = []
    grok_tools = function_tools(listed, settings.allowlist)
    await connection.grok.send(json.dumps(build_session(settings, grok_tools)))
    await websocket.send(json.dumps({"type": "ready", "sampleRate": 24000}))

    bridge = GrokBridge(gateway)
    idle = asyncio.Event()
    activity = asyncio.Event()

    async def touch() -> None:
        activity.set()

    async def pump_client() -> None:
        async for incoming in websocket:
            await touch()
            if isinstance(incoming, bytes):
                await connection.grok.send(json.dumps(bridge.client_audio(incoming)))
                continue
            try:
                control = json.loads(incoming)
            except json.JSONDecodeError:
                continue
            if control.get("type") == "stop":
                idle.set()
                return

    async def pump_grok() -> None:
        async for incoming in connection.grok:
            await touch()
            if isinstance(incoming, bytes):
                bridge.playing = True
                await websocket.send(incoming)
                continue
            event = json.loads(incoming)
            if event.get("type") == "response.function_call_arguments.done":
                output = await bridge.handle_function_call(event)
                denied = "tool_not_allowed" in output["item"]["output"]
                await websocket.send(
                    json.dumps(
                        {
                            "type": "tool",
                            "name": event.get("name"),
                            "status": "denied" if denied else "done",
                        }
                    )
                )
                await connection.grok.send(json.dumps(output))
                continue
            if event.get("type") == "response.done":
                followup = bridge.followup_after_tools()
                if followup:
                    await connection.grok.send(json.dumps(followup))
                continue
            for client_event in bridge.client_messages(event):
                if client_event.get("type") == "binary":
                    await websocket.send(client_event["pcm"])
                else:
                    await websocket.send(json.dumps(client_event))

    async def watch_idle() -> None:
        while not idle.is_set():
            activity.clear()
            try:
                await asyncio.wait_for(activity.wait(), timeout=settings.idle_timeout_seconds)
            except asyncio.TimeoutError:
                idle.set()
                return

    client_task = asyncio.create_task(pump_client())
    grok_task = asyncio.create_task(pump_grok())
    idle_task = asyncio.create_task(watch_idle())
    done, pending = await asyncio.wait(
        {client_task, grok_task, idle_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        with contextlib.suppress(Exception):
            task.result()
    reason = "idle"
    if client_task.done() and not client_task.cancelled():
        reason = "stop"
    if grok_task.done() and not grok_task.cancelled() and grok_task.exception() is not None:
        reason = "error"
        log.error("grok connection ended: %s", type(grok_task.exception()).__name__)
    await _end(websocket, reason)
    with contextlib.suppress(Exception):
        await connection.grok.close()


async def _end(websocket, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send(json.dumps({"type": "end", "reason": reason}))
    with contextlib.suppress(Exception):
        await websocket.close()


async def serve_voice(settings, http) -> None:
    async def handler(websocket):
        await handle_socket(websocket, settings, http)

    async def process_request(connection, request):
        upgrade = request.headers.get("Upgrade")
        if upgrade is None or str(upgrade).lower() != "websocket":
            return connection.respond(200, "Grok Voice agent\n")
        return None

    servers = [
        await serve(handler, "0.0.0.0", settings.ingress_port, process_request=process_request),
        await serve(handler, "0.0.0.0", settings.debug_port, process_request=process_request),
    ]
    log.info(
        "listening for dashboard ingress on %s and debug on %s",
        settings.ingress_port,
        settings.debug_port,
    )
    await asyncio.Future()
    for server in servers:
        server.close()
