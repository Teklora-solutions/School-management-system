"""
apps/assistant/providers/http.py
--------------------------------
A tiny JSON-over-HTTPS helper. No key is ever logged; error messages are capped
and never include request headers.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


class HttpStatusError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = (message or "")[:300]
        super().__init__(f"HTTP {status}")


class TransportTimeout(Exception):
    pass


class TransportNetworkError(Exception):
    pass


def post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str],
    timeout: float = 30,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        message = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8", "replace"))
            message = _extract_error_message(parsed)
        except (ValueError, OSError):
            message = ""
        raise HttpStatusError(exc.code, message) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise TransportTimeout() from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TransportTimeout() from exc
        raise TransportNetworkError(str(exc.reason)[:200]) from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ValueError("The provider returned a non-JSON reply.") from exc
    if not isinstance(data, dict):
        raise ValueError("The provider returned an unexpected reply shape.")
    return data


def _extract_error_message(payload) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("status") or "")
        if isinstance(error, str):
            return error
    return ""
