"""Validate a dashboard access token before any Grok socket is opened."""

from __future__ import annotations


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


class HaAuth:
    def __init__(self, api_url: str, http) -> None:
        self.api_url = api_url.rstrip("/")
        self.http = http

    async def validate(self, token: str) -> bool:
        if not token or not token.strip():
            return False
        response = await self.http.get(
            f"{self.api_url}/api/",
            headers={"Authorization": f"Bearer {token}"},
        )
        return getattr(response, "status_code", None) == 200
