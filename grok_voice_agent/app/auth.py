"""Validate a dashboard access token before any Grok socket is opened."""

from __future__ import annotations

# Supervisor's ingress proxy always connects from this address, including
# when the add-on uses host networking. Headers are only meaningful from it.
SUPERVISOR_INGRESS_PEER = "172.30.32.2"
INGRESS_SOURCE = "core.ingress"


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def header_value(headers, name: str) -> str:
    if headers is None:
        return ""
    raw = headers.get(name) if hasattr(headers, "get") else None
    if raw is None and hasattr(headers, "items"):
        wanted = name.lower()
        for key, value in headers.items():
            if str(key).lower() == wanted:
                raw = value
                break
    if raw is None:
        return ""
    return str(raw).strip()


def peer_host(remote_address: object) -> str:
    if remote_address is None:
        return ""
    host = remote_address[0] if isinstance(remote_address, tuple) else remote_address
    text = str(host)
    if text.startswith("::ffff:"):
        return text[7:]
    return text


def trusted_ingress_user(headers, remote_address: object) -> str | None:
    """Return the signed-in user id when this socket is Supervisor ingress.

    Home Assistant Core marks the hop ``X-Hass-Source: core.ingress``.
    Supervisor then adds ``X-Remote-User-Id`` only after the browser's
    ingress session cookie checks out. Those headers can be forged by
    anyone who can open the port, so they count only from the Supervisor
    container itself.
    """
    if peer_host(remote_address) != SUPERVISOR_INGRESS_PEER:
        return None
    if header_value(headers, "X-Hass-Source") != INGRESS_SOURCE:
        return None
    # Supervisor already checked the ingress_session cookie before this hop.
    # The user id is attached when Core sent one; a valid session without it
    # is still the signed-in Open Web UI.
    return header_value(headers, "X-Remote-User-Id") or "ingress"


class HaAuth:
    def __init__(self, api_url: str, http) -> None:
        self.api_url = api_url.rstrip("/")
        self.http = http

    async def validate(self, token: str) -> bool:
        if not token or not token.strip():
            return False
        # ``api_url`` must be Home Assistant Core (loopback on host_network).
        # ``http://supervisor/core/api/`` treats the bearer as the add-on
        # token and answers 401 for a user access token.
        response = await self.http.get(
            f"{self.api_url}/api/",
            headers={"Authorization": f"Bearer {token}"},
        )
        return getattr(response, "status_code", None) == 200
