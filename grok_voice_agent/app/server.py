"""Browser voice socket. The Grok connection opens only after HA accepts the token."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from websockets.asyncio.client import connect as connect_grok
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from app.auth import HaAuth, redact, trusted_ingress_user
from app.grok_session import (
    ConversationWatch,
    GrokBridge,
    build_session,
    is_closing_utterance,
    is_end_session_tool,
    merge_session_area,
    parse_client_device,
    xai_realtime_error_log,
    xai_session_tools_log,
)
from app.ha_context import (
    CONFIG_FETCH_TIMEOUT_SECONDS,
    fallback_home_context,
    fetch_home_context,
    merge_home_location,
)
from app.mcp_client import McpHttpClient, function_tools, voice_tool_log
from app.memory import ConversationMemory, SessionTranscript, conversation_key, history_conversation_events
from app.static import process_http_request
from app.tools import ToolGateway

log = logging.getLogger("grok_voice")
GROK_URL = "wss://api.x.ai/v1/realtime"


class VoiceConnection:
    def __init__(self, settings, http, grok_connect=None) -> None:
        self.settings = settings
        self.http = http
        self.grok_connect = grok_connect or _default_grok_connect
        self.grok = None

    async def open_grok(self) -> None:
        if self.grok is None:
            self.grok = await self.grok_connect(self.settings)

    async def authenticate(self, token: str) -> bool:
        auth = HaAuth(self.settings.ha_api_url, self.http)
        if not await auth.validate(token):
            return False
        await self.open_grok()
        return True


def ingress_user_from_socket(websocket, ingress_port: int) -> str | None:
    """Signed-in ingress user, only on the ingress port.

    The debug port keeps the long-lived token check even if a client copies
    ingress headers onto that socket.
    """
    local = getattr(websocket, "local_address", None)
    local_port = local[1] if isinstance(local, tuple) and len(local) > 1 else None
    if local_port != ingress_port:
        return None
    request = getattr(websocket, "request", None)
    headers = getattr(request, "headers", None) if request is not None else None
    return trusted_ingress_user(headers, getattr(websocket, "remote_address", None))


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


async def _home_context(http, settings):
    try:
        context = await asyncio.wait_for(
            fetch_home_context(http, settings),
            timeout=CONFIG_FETCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log.info("home context fallback (%s)", type(exc).__name__)
        context = fallback_home_context(settings)
    return merge_home_location(context, settings)


async def _list_tools(gateway):
    try:
        return await gateway.mcp.list_tools()
    except Exception as exc:
        log.exception("MCP tools/list failed: %s", exc)
        return []


async def handle_socket(websocket, settings, http, grok_connect=None, memory=None) -> None:
    connection = VoiceConnection(settings, http, grok_connect)
    ingress_user = ingress_user_from_socket(websocket, settings.ingress_port)
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
        if ingress_user:
            await connection.open_grok()
            authorized = True
        else:
            authorized = await connection.authenticate(str(token or ""))
    except Exception as exc:
        log.error("voice session failed before it was ready: %s", type(exc).__name__)
        await _end(websocket, "error")
        return
    if not authorized:
        log.info("voice auth rejected")
        await _end(websocket, "unauthorized")
        return

    if ingress_user:
        log.info("voice session authenticated via ingress")
    else:
        log.info("voice session authenticated")
    area = merge_session_area(message.get("area"), settings)
    device = parse_client_device(message.get("device"))
    store = memory if memory is not None else ConversationMemory(settings.conversation_memory_ttl_seconds)
    memory_key = conversation_key(device, area)
    history = store.get(memory_key)
    if area:
        log.info("voice area name=%s id=%s", area.get("name"), area.get("id") or "")
    if device:
        log.info("voice device name=%s id=%s", device.get("name"), device.get("id") or "")
    if history:
        log.info("voice memory key=%s turns=%s", memory_key, len(history))
    gateway = ToolGateway(
        McpHttpClient(settings.ha_mcp_url, settings.mcp_token, http),
        settings.allowlist,
        default_area=area,
    )
    listed, context = await asyncio.gather(_list_tools(gateway), _home_context(http, settings))
    grok_tools = function_tools(listed, settings.allowlist)
    level, tool_message = voice_tool_log(listed, grok_tools)
    getattr(log, level)(tool_message)
    log.info(
        "voice context tz=%s home_location=%s city=%s",
        context.time_zone,
        context.home_location or "(unset)",
        context.home_location or context.zone_name or context.location_name or "",
    )
    await connection.grok.send(
        json.dumps(build_session(settings, grok_tools, area, context=context, history=history))
    )
    for event in history_conversation_events(history):
        await connection.grok.send(json.dumps(event))
    await websocket.send(
        json.dumps(
            {
                "type": "ready",
                "sampleRate": 24000,
                "idleTimeoutSeconds": settings.idle_timeout_seconds,
            }
        )
    )

    bridge = GrokBridge(gateway)
    watch = ConversationWatch()
    transcript = SessionTranscript()
    idle = asyncio.Event()
    activity = asyncio.Event()
    end_reason = "idle"
    forget_memory = False

    def request_end(reason: str, *, forget: bool | None = None) -> None:
        nonlocal end_reason, forget_memory
        if idle.is_set():
            return
        end_reason = reason
        if forget is not None:
            forget_memory = forget
        elif reason == "done":
            forget_memory = True
        idle.set()
        activity.set()

    def mark_turn() -> None:
        activity.set()

    def persist_turns() -> None:
        if transcript.turns:
            store.remember(memory_key, transcript.turns)

    async def pump_client() -> None:
        async for incoming in websocket:
            if isinstance(incoming, bytes):
                await connection.grok.send(json.dumps(bridge.client_audio(incoming)))
                continue
            try:
                control = json.loads(incoming)
            except json.JSONDecodeError:
                continue
            if control.get("type") == "stop":
                request_end(str(control.get("reason") or "stop"))
                return

    async def pump_grok() -> None:
        nonlocal forget_memory
        async for incoming in connection.grok:
            if isinstance(incoming, bytes):
                bridge.playing = True
                watch.on_response_started()
                mark_turn()
                await websocket.send(incoming)
                continue
            event = json.loads(incoming)
            event_type = event.get("type")
            error_line = xai_realtime_error_log(event)
            if error_line:
                log.error("%s", error_line)
            session_line = xai_session_tools_log(event)
            if session_line:
                log.info("%s", session_line)
            if event_type == "input_audio_buffer.speech_started":
                watch.on_speech_started()
                mark_turn()
            elif event_type == "input_audio_buffer.speech_stopped":
                watch.on_speech_stopped()
                mark_turn()
                transcript.flush_pending("user")
                persist_turns()
            elif event_type == "response.created":
                watch.on_response_started()
                mark_turn()
            if event_type == "response.function_call_arguments.done":
                output = await bridge.handle_function_call(event)
                denied = "tool_not_allowed" in output["item"]["output"]
                name = str(event.get("name") or "")
                if is_end_session_tool(name):
                    log.info(
                        "voice end_session reason=%s home_control=%s ending=%s",
                        "dismiss" if bridge.end_session_forget else "command",
                        bridge.home_control_this_turn,
                        bridge.end_after_response,
                    )
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
            if event_type == "response.done":
                followup = bridge.followup_after_tools()
                watch.on_response_done(awaiting_tools=followup is not None)
                mark_turn()
                if followup:
                    await connection.grok.send(json.dumps(followup))
                    continue
                transcript.flush_pending("assistant")
                persist_turns()
                await websocket.send(json.dumps({"type": "response_done"}))
                if bridge.consume_end_session():
                    request_end("done", forget=bridge.end_session_forget)
                continue
            for client_event in bridge.client_messages(event):
                if client_event.get("type") == "binary":
                    await websocket.send(client_event["pcm"])
                    continue
                await websocket.send(json.dumps(client_event))
                if client_event.get("type") == "transcript":
                    transcript.add(
                        client_event.get("role"),
                        client_event.get("text"),
                        bool(client_event.get("final")),
                    )
                    if client_event.get("final"):
                        persist_turns()
                if (
                    client_event.get("type") == "transcript"
                    and client_event.get("role") == "user"
                    and client_event.get("final")
                    and is_closing_utterance(str(client_event.get("text") or ""))
                ):
                    # Wait for the ack turn (same as end_session) so Grok can
                    # finish speaking before the duplex closes. Forget only if
                    # consume_end_session actually hangs up (a follow-up question
                    # keeps the session open).
                    bridge.note_closing_phrase()

    async def watch_idle() -> None:
        while not idle.is_set():
            while not watch.is_quiet() and not idle.is_set():
                activity.clear()
                await activity.wait()
            if idle.is_set():
                return
            activity.clear()
            try:
                await asyncio.wait_for(
                    activity.wait(),
                    timeout=bridge.idle_timeout_seconds(settings.idle_timeout_seconds),
                )
            except asyncio.TimeoutError:
                if watch.is_quiet() and not idle.is_set():
                    request_end("idle")
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
    reason = end_reason
    if grok_task.done() and not grok_task.cancelled() and grok_task.exception() is not None:
        reason = "error"
        log.error("grok connection ended: %s", type(grok_task.exception()).__name__)
    elif client_task.done() and not client_task.cancelled() and end_reason == "idle":
        reason = "stop"
    if forget_memory:
        store.forget(memory_key)
    else:
        transcript.flush_pending()
        persist_turns()
    await _end(websocket, reason)
    with contextlib.suppress(Exception):
        await connection.grok.close()


async def _end(websocket, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send(json.dumps({"type": "end", "reason": reason}))
    with contextlib.suppress(Exception):
        await websocket.close()


def drop_empty_content_length(header_block: bytes) -> bytes:
    """Remove ``Content-Length: 0`` from an HTTP/1.1 header block.

    Supervisor's ingress handler forwards the browser GET with
    ``data=await request.read()``. That body is empty, so aiohttp writes
    ``Content-Length: 0``. ``websockets`` rejects every Content-Length
    before ``process_request`` and aborts the socket, which Supervisor
    surfaces as ``502: Bad Gateway``. The Open Web UI treats that page as
    not ready. A direct browser GET has no Content-Length, so port 8080
    still serves the mic page.
    """
    lines = header_block.split(b"\r\n")
    kept = [lines[0]]
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        if separator and name.lower() == b"content-length" and value.strip() == b"0":
            continue
        kept.append(line)
    return b"\r\n".join(kept)


class IngressServerConnection(ServerConnection):
    """WebSocket connection that still answers Supervisor's ingress GET."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ingress_pending = b""
        self._ingress_headers_done = False

    def data_received(self, data: bytes) -> None:
        if self._ingress_headers_done:
            super().data_received(data)
            return
        self._ingress_pending += data
        separator = self._ingress_pending.find(b"\r\n\r\n")
        if separator == -1:
            if len(self._ingress_pending) > 65536:
                self._ingress_headers_done = True
                pending = self._ingress_pending
                self._ingress_pending = b""
                super().data_received(pending)
            return
        head = self._ingress_pending[:separator]
        rest = self._ingress_pending[separator + 4 :]
        self._ingress_pending = b""
        self._ingress_headers_done = True
        super().data_received(drop_empty_content_length(head) + b"\r\n\r\n" + rest)


def voice_serve(handler, host: str, port: int, process_request):
    """Listen on one voice port, accepting ingress probes and WebSockets."""
    return serve(
        handler,
        host,
        port,
        process_request=process_request,
        create_connection=IngressServerConnection,
    )


async def serve_voice(settings, http) -> None:
    memory = ConversationMemory(settings.conversation_memory_ttl_seconds)

    async def handler(websocket):
        await handle_socket(websocket, settings, http, memory=memory)

    async def process_request(_connection, request):
        return process_http_request(request)

    servers = [
        await voice_serve(handler, "0.0.0.0", settings.ingress_port, process_request),
        await voice_serve(handler, "0.0.0.0", settings.debug_port, process_request),
    ]
    log.info(
        "listening for dashboard ingress on %s and debug on %s",
        settings.ingress_port,
        settings.debug_port,
    )
    await asyncio.Future()
    for server in servers:
        server.close()
