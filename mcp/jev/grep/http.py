"""Bounded HTTP policy for every provider transport (port of upstream evaluation/http.ts).

Redirects refused (credentials never replayed to another host), response bodies
capped, invalid UTF-8 rejected instead of replaced. Error classification uses the
status and Retry-After only; a provider error body never reaches the caller.
"""
import http.client
import json
import ssl
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

MAX_PROVIDER_RESPONSE_BYTES = 8 * 1_024 * 1_024


class ProviderBodyLimitError(Exception):
    pass


class TransportCancelled(Exception):
    """The caller's stop signal fired; no result may be emitted."""


class TransportResponse:
    def __init__(self, status: int, headers: Dict[str, str], text: str):
        self.status = status
        self.headers = headers  # lower-cased keys
        self.text = text


def bounded_request(url: str, headers: Dict[str, str], body: str, timeout: float,
                    is_cancelled=None) -> TransportResponse:
    """One bounded exchange. 3xx is a response, never a follow."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("only https provider endpoints are supported")
    connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout,
                                             context=ssl.create_default_context())
    try:
        if is_cancelled is not None and is_cancelled():
            raise TransportCancelled()
        connection.request("POST", parts.path or "/", body=body.encode("utf-8"), headers=headers)
        raw = connection.getresponse()
        status = raw.status
        response_headers = {key.lower(): value for key, value in raw.getheaders()}

        if not (200 <= status < 300):
            # ponytail: never read an error body; close() discards it and
            # classification needs status and headers only
            if is_cancelled is not None and is_cancelled():
                raise TransportCancelled()
            return TransportResponse(status, response_headers, "{}" if status != 304 else "")

        declared = response_headers.get("content-length")
        if declared is not None and int(declared) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderBodyLimitError("provider response exceeds the local byte limit")
        chunks = []
        size = 0
        while True:
            if is_cancelled is not None and is_cancelled():
                raise TransportCancelled()
            part = raw.read(min(65_536, MAX_PROVIDER_RESPONSE_BYTES + 1 - size))
            if not part:
                break
            size += len(part)
            if size > MAX_PROVIDER_RESPONSE_BYTES:
                raise ProviderBodyLimitError("provider response exceeds the local byte limit")
            chunks.append(part)
        data = b"".join(chunks)
        try:
            text = data.decode("utf-8")  # strict: no manufactured replacement characters
        except UnicodeDecodeError:
            raise ProviderBodyLimitError("provider response is not valid UTF-8") from None
        return TransportResponse(status, response_headers, text if status not in (204, 205, 304) else "")
    finally:
        connection.close()
