"""Deny-by-default secrets egress proxy primitives (OC-D08).

Qubit never runs a local forwarding network server (see
:func:`voicebridge.proxy_status.refuse_start`): a forwarder would bypass
minimum-necessary egress policy and audit. Instead, brokered outbound calls
may use this module to substitute secret placeholders into a request stream
**only** when the destination host is on an explicit allowlist.

Security contract:

* **Deny by default.** No host is allowed unless it is explicitly listed (or
  the operator sets ``allow_all`` and accepts the risk). A denied destination
  is a typed refusal and no substitution happens.
* **References, never values.** The registry stores only ``env:`` / ``file:``
  references. Values are resolved at call time and never persisted, logged,
  returned in status, or embedded in an error message.
* **Fail closed.** An unknown ``{{secret:NAME}}`` placeholder, a missing
  secret, a malformed reference, or a placeholder split across stream chunks
  that never closes is a typed error, not a silent passthrough.
* **Stream aware.** Substitution works across arbitrary chunk boundaries
  without leaking a partially substituted placeholder downstream.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

SCHEMA = "qubit-secrets-egress/1"

# Typed codes (never a bare exception across a boundary).
EGRESS_DENIED = "EGRESS_DENIED"
EGRESS_SECRET_MISSING = "EGRESS_SECRET_MISSING"
EGRESS_UNKNOWN_PLACEHOLDER = "EGRESS_UNKNOWN_PLACEHOLDER"
EGRESS_BAD_REFERENCE = "EGRESS_BAD_REFERENCE"
EGRESS_INCOMPLETE_PLACEHOLDER = "EGRESS_INCOMPLETE_PLACEHOLDER"

MAX_SECRETS = 64
MAX_NAME = 64
MAX_VALUE_BYTES = 64 * 1024

_PLACEHOLDER = re.compile(r"\{\{secret:([A-Za-z0-9_.\-]{1,64})\}\}")
_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_REF_OK = re.compile(r"^(env|file):.{1,1024}$")


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


class EgressError(Exception):
    """Typed egress failure (never a traceback at a boundary)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _kind(reference: str) -> str:
    if reference.startswith("env:"):
        return "env"
    if reference.startswith("file:"):
        return "file"
    return "missing"


class SecretRegistry:
    """A tiny registry of secret *references* (never values)."""

    def __init__(self, *, environ: Optional[Mapping[str, str]] = None,
                 max_secrets: int = MAX_SECRETS) -> None:
        self._refs: Dict[str, str] = {}
        self._environ = environ
        self._max = int(max_secrets)
        self.rejections = 0

    # -------------------------------------------------------------- registry
    def register(self, name: str, reference: str) -> Dict[str, Any]:
        """Register ``name`` -> ``env:VAR`` / ``file:PATH`` (reference only)."""
        if not _NAME_OK.match(str(name or "")):
            self.rejections += 1
            raise EgressError(EGRESS_BAD_REFERENCE, "invalid secret name")
        ref = str(reference or "").strip()
        if not _REF_OK.match(ref):
            self.rejections += 1
            raise EgressError(
                EGRESS_BAD_REFERENCE,
                "reference must be env:VAR or file:PATH (values are never "
                "accepted)")
        if len(self._refs) >= self._max and name not in self._refs:
            self.rejections += 1
            raise EgressError(EGRESS_BAD_REFERENCE,
                              "secret registry is full")
        self._refs[name] = ref
        return {"name": name, "kind": _kind(ref)}

    def unregister(self, name: str) -> bool:
        return self._refs.pop(name, None) is not None

    def names(self) -> List[str]:
        return sorted(self._refs)

    def reference(self, name: str) -> str:
        return self._refs.get(name, "")

    def __contains__(self, name: object) -> bool:
        return name in self._refs

    def __len__(self) -> int:
        return len(self._refs)

    # --------------------------------------------------------------- resolve
    def resolve(self, name: str) -> str:
        """Resolve one secret to its value (never logged or returned in status).

        Raises :class:`EgressError` with ``EGRESS_SECRET_MISSING`` when the
        name is unknown or the reference cannot be read. The exception detail
        never contains the value.
        """
        ref = self._refs.get(name)
        if not ref:
            raise EgressError(EGRESS_SECRET_MISSING,
                              "secret is not registered")
        kind = _kind(ref)
        if kind == "env":
            var = ref[4:]
            if self._environ is not None:
                value = self._environ.get(var)
            else:
                value = os.environ.get(var)
            if not value:
                raise EgressError(EGRESS_SECRET_MISSING,
                                  "secret reference is not set")
            return value
        if kind == "file":
            path = Path(ref[5:]).expanduser()
            try:
                if path.stat().st_size > MAX_VALUE_BYTES:
                    raise EgressError(EGRESS_SECRET_MISSING,
                                      "secret file is too large")
                value = path.read_text(errors="replace").strip()
            except EgressError:
                raise
            except OSError:
                raise EgressError(EGRESS_SECRET_MISSING,
                                  "secret file is unreadable")
            if not value:
                raise EgressError(EGRESS_SECRET_MISSING,
                                  "secret file is empty")
            return value
        raise EgressError(EGRESS_BAD_REFERENCE, "unsupported reference scheme")

    def status(self) -> Dict[str, Any]:
        """Names + reference kinds only; values never appear here."""
        return {
            "schema": SCHEMA,
            "count": len(self._refs),
            "secrets": [{"name": n, "kind": _kind(self._refs[n])}
                        for n in self.names()],
            "rejections": self.rejections,
        }


class EgressPolicy:
    """Explicit allowlist for outbound destinations (deny by default)."""

    def __init__(self, *, allowed_hosts: Iterable[str] = (),
                 allow_all: bool = False) -> None:
        self.allow_all = bool(allow_all)
        self._allowed = {self._norm(h) for h in allowed_hosts if str(h).strip()}
        self._allowed.discard("")

    @staticmethod
    def _norm(host: str) -> str:
        h = str(host).strip().lower()
        if "://" in h:
            h = h.split("://", 1)[1]
        if "@" in h:  # strip any userinfo
            h = h.rsplit("@", 1)[1]
        h = h.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if h.startswith("[") and "]" in h:  # IPv6 literal
            h = h[1:h.index("]")]
        elif h.count(":") == 1:  # host:port
            h = h.split(":", 1)[0]
        return h.rstrip(".")

    @property
    def allowed_hosts(self) -> List[str]:
        return sorted(self._allowed)

    def decide(self, host: str) -> Tuple[bool, str]:
        if not str(host).strip():
            return False, EGRESS_DENIED
        if self.allow_all:
            return True, "ALLOW_ALL"
        if self._norm(host) in self._allowed:
            return True, "ALLOWLISTED"
        return False, EGRESS_DENIED

    def check(self, host: str) -> Dict[str, Any]:
        allowed, code = self.decide(host)
        return {"allowed": allowed, "code": "OK" if allowed else code,
                "host": _redact(str(host)), "schema": SCHEMA}

    @classmethod
    def from_config(cls, cfg: Any) -> "EgressPolicy":
        get = getattr(cfg, "get", None)
        hosts: Iterable[str] = ()
        allow_all = False
        if callable(get):
            try:
                hosts = get("proxy", "allowed_hosts", default=()) or ()
                allow_all = bool(get("proxy", "allow_all", default=False))
            except TypeError:
                pass
        return cls(allowed_hosts=hosts, allow_all=allow_all)


# ------------------------------------------------------------- substitution
def placeholders(text: str) -> List[str]:
    """Names referenced by ``{{secret:NAME}}`` in ``text`` (no values)."""
    return [m.group(1) for m in _PLACEHOLDER.finditer(str(text or ""))]


def _is_partial_placeholder(inner: str) -> bool:
    if len(inner) <= len("secret:"):
        return "secret:".startswith(inner)
    if not inner.startswith("secret:"):
        return False
    rest = inner[len("secret:"):]
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-]{0,64}", rest))


def _partial_start(buffer: str) -> int:
    """Index of a trailing, still-open ``{{secret:`` placeholder, or -1.

    A trailing ``{{`` that can no longer become a secret placeholder is left
    alone (it is ordinary text), so only genuine partials are held.
    """
    start = buffer.rfind("{{")
    while start != -1:
        if "}}" not in buffer[start:]:
            if _is_partial_placeholder(buffer[start + 2:]):
                return start
            return -1
        start = buffer.rfind("{{", 0, start)
    return -1


def _substitute(text: str, registry: SecretRegistry) -> str:
    def repl(match: "re.Match[str]") -> str:
        return registry.resolve(match.group(1))

    return _PLACEHOLDER.sub(repl, text)


def substitute(text: str, policy: EgressPolicy, registry: SecretRegistry,
               host: str) -> str:
    """Substitute complete placeholders for one allowed destination.

    Raises a typed :class:`EgressError` for a denied host, an unknown or
    incomplete placeholder, or a missing secret. The caller must not catch
    and continue — a failed substitution must never fall through to the wire.
    """
    allowed, code = policy.decide(host)
    if not allowed:
        raise EgressError(EGRESS_DENIED,
                          f"destination is not allowlisted ({code})")
    if _partial_start(text) != -1:
        raise EgressError(EGRESS_INCOMPLETE_PLACEHOLDER,
                          "incomplete secret placeholder in request")
    return _substitute(text, registry)


def substitute_stream(chunks: Iterable[str], policy: EgressPolicy,
                      registry: SecretRegistry, host: str) -> Iterator[str]:
    """Stream substitute across arbitrary chunk boundaries.

    Complete placeholders are replaced as soon as their ``}}`` is seen; a
    trailing partial placeholder is held until the next chunk. If the stream
    ends with an unclosed placeholder the generator raises
    ``EGRESS_INCOMPLETE_PLACEHOLDER`` instead of emitting a half-substituted
    payload.
    """
    allowed, code = policy.decide(host)
    if not allowed:
        raise EgressError(EGRESS_DENIED,
                          f"destination is not allowlisted ({code})")
    buffer = ""
    for chunk in chunks:
        if not isinstance(chunk, str):
            chunk = str(chunk)
        buffer += chunk
        out, buffer = _drain(buffer, registry)
        if out:
            yield out
    if buffer:
        if _partial_start(buffer) != -1:
            raise EgressError(EGRESS_INCOMPLETE_PLACEHOLDER,
                              "stream ended with an incomplete secret "
                              "placeholder")
        yield _substitute(buffer, registry)


def _drain(buffer: str, registry: SecretRegistry) -> Tuple[str, str]:
    """Substitute complete placeholders; return (output, held remainder)."""
    idx = _partial_start(buffer)
    hold_from = idx if idx != -1 else len(buffer)
    head = buffer[:hold_from]
    remainder = buffer[hold_from:]
    return _substitute(head, registry), remainder


# ------------------------------------------------------------------- audit
def audit_egress(op: str, host: str, allowed: bool, *,
                 secret_names: Iterable[str] = ()) -> None:
    """Best-effort audit event that never carries a secret value."""
    from .logging_setup import log_event

    try:
        log_event(
            f"proxy_secrets.{op}",
            actor="proxy",
            host=_redact(str(host)),
            allowed=bool(allowed),
            secret_names=sorted(set(secret_names)),
        )
    except Exception:
        pass
