"""The only network client for the Etsy OAuth flow.

Kept separate from :mod:`voicebridge.integrations.etsy_auth` so the OAuth state
machine stays pure and testable with a fake transport. Nothing instantiates this
class except the Office bridge at the moment a callback/refresh is actually
performed; tests inject their own fake and never reach this module.

Safety properties:

* HTTPS only (a loopback HTTP URL is allowed solely for an explicit test host);
* form-encoded body, bounded response size, bounded timeout;
* the response body is parsed as JSON and never echoed into an error message,
  so a provider error can never copy a token into a payload or log;
* no cookies, redirects, or credentials beyond the OAuth form fields.
"""
from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import urlencode, urlparse

MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0


class EtsyTransportError(Exception):
    """A transport failure whose message never contains provider data."""


def _loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


class EtsyOAuthHttpTransport:
    """A minimal form-POST JSON transport for the Etsy token endpoint."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        allow_loopback_http: bool = False,
    ) -> None:
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self.allow_loopback_http = bool(allow_loopback_http)

    def request(
        self,
        method: str,
        url: str,
        *,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        if str(method or "").upper() != "POST":
            raise EtsyTransportError("the Etsy token endpoint requires POST")
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http"
            and self.allow_loopback_http
            and _loopback(parsed.hostname or "")
        ):
            raise EtsyTransportError("the Etsy token endpoint must use HTTPS")
        if not parsed.netloc:
            raise EtsyTransportError("the Etsy token endpoint URL is invalid")

        body = urlencode(dict(form or {})).encode("utf-8")
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        request_headers.update({str(k): str(v) for k, v in (headers or {}).items()})

        # Imported lazily so importing the OAuth state machine never pulls in a
        # network module.
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        request = Request(url, data=body, method="POST", headers=request_headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read(self.max_bytes + 1)
        except HTTPError as exc:
            # Never read or include the body: it may echo request material.
            raise EtsyTransportError(
                f"the Etsy token endpoint answered HTTP {int(exc.code)}"
            ) from exc
        except URLError as exc:
            raise EtsyTransportError(
                f"the Etsy token endpoint could not be reached"
                f" ({type(exc.reason).__name__})"
            ) from exc
        except OSError as exc:
            raise EtsyTransportError(
                f"the Etsy token request failed ({type(exc).__name__})"
            ) from exc

        if len(raw) > self.max_bytes:
            raise EtsyTransportError("the Etsy token response was too large")
        try:
            parsed_body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise EtsyTransportError(
                "the Etsy token endpoint returned a non-JSON response"
            ) from exc
        if not isinstance(parsed_body, dict):
            raise EtsyTransportError(
                "the Etsy token endpoint returned a non-object response"
            )
        return parsed_body


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "EtsyOAuthHttpTransport",
    "EtsyTransportError",
    "MAX_RESPONSE_BYTES",
]
