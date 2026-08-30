"""Packet 8E: website-integration onboarding and observability (non-live).

A pasted URL creates only a pending, disabled, versioned integration
PROPOSAL. Discovery is bounded read-only and returns redacted metadata plus a
reviewable capability proposal; it never grants access, trains the model,
executes page-provided instructions, imports credentials, clones a profile,
launches a Playwright personal profile, or creates a hidden skill. There is no
live site adapter here -- that remains a separately authorized packet.

Facts enforced here:

- Only http/https origins, no credentials, no query-token URLs, no private
  networks (SSRF blocked), no out-of-scope origins.
- ``restricted`` permission mode is the default; grants are created ONLY for
  capabilities in the validated proposal, scoped to the integration origin,
  expiring, confirmed, and audited, and ONLY after an explicit user action
  links a valid, installed, ACTIVE 7E skill manifest (present, re-validated,
  licensed, hash-valid, compatible). Page/model/tool text can never widen a
  grant or change policy.
- ``manifest_link`` is written ONLY by the explicit ``link_manifest``
  operation; a website record stays unlinked until the user links a valid
  installed 7E skill. Enable/grant fail closed without an active link.
- Every transition writes a redacted audit event (timestamp, integration ID,
  operation, status, reason code, duration, bounded counts, revision,
  evidence availability). Logs never contain cookies, tokens, full private
  page text, or secret URLs.
- State persists as a private atomic JSON file with timestamped backups and a
  bounded audit log, mirroring the Packet 7E permission store contract.

This module executes nothing on a page and opens no browser/service. The
network probe is fully injectable so tests stay hermetic.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urljoin
from urllib.request import Request, urlopen

from .capabilities import CapabilitySpec, Tier
from .logging_setup import log_event
from .redaction import redact_obj, redact_text

# ---------------------------------------------------------------------------
# Typed lifecycle states (Packet 8E contract)
# ---------------------------------------------------------------------------
STATUS_PENDING = "PENDING"
STATUS_DISCOVERING = "DISCOVERING"
STATUS_READY = "READY"
STATUS_AUTH_REQUIRED = "AUTH_REQUIRED"
STATUS_DENIED = "DENIED"
STATUS_SELECTOR_DRIFT = "SELECTOR_DRIFT"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"
STATUS_DISABLED = "DISABLED"
STATUS_REMOVED = "REMOVED"

STATUSES = frozenset({
    STATUS_PENDING, STATUS_DISCOVERING, STATUS_READY, STATUS_AUTH_REQUIRED,
    STATUS_DENIED, STATUS_SELECTOR_DRIFT, STATUS_DEGRADED, STATUS_FAILED,
    STATUS_DISABLED, STATUS_REMOVED,
})

# Lifecycle states that can never carry a capability proposal.
_NO_PROPOSAL = frozenset({
    STATUS_AUTH_REQUIRED, STATUS_DENIED, STATUS_SELECTOR_DRIFT,
    STATUS_DEGRADED, STATUS_FAILED, STATUS_REMOVED,
})

SCHEMA_VERSION = 1
AUDIT_CAP = 400

# Bounded limits applied to the read-only discovery probe.
DEFAULT_TIMEOUT_S = 8.0
DEFAULT_MAX_BYTES = 65_536
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_GRANT_TTL_S = 7 * 24 * 3600   # website grants are expiring by default

MAX_TITLE = 120
MAX_DISPLAY_NAME = 120
MAX_REASON = 200
MAX_HINTS = 16

# Only these READ_ONLY observation capabilities can ever be PROPOSED. There is
# no executor for them in Packet 8E; a later adapter packet must register and
# gate them before anything can run.
OBSERVATION_CAPABILITIES: Dict[str, str] = {
    "web.observe.reachability":
        "observe whether an origin is reachable and its auth state",
    "web.observe.metadata":
        "read bounded safe public metadata (title) from an origin page",
}

_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_./:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")

# Deterministic local SPDX allowlist (fail closed: unknown tokens are rejected).
# Covers common OSI/FSF identifiers used by software artifacts; license
# EXPRESSIONS are parsed with AND/OR/WITH and optional `+` suffixes.
SPDX_LICENSES = frozenset({
    "0BSD", "AFL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-1.1",
    "Apache-2.0", "Artistic-2.0", "BSD-2-Clause", "BSD-3-Clause",
    "BSD-4-Clause", "BSL-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0",
    "CDDL-1.0", "CDDL-1.1", "CPL-1.0", "ECL-2.0", "EPL-1.0", "EPL-2.0",
    "EUPL-1.2", "GFDL-1.3-only", "GFDL-1.3-or-later", "GPL-2.0-only",
    "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "ISC",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only",
    "LGPL-3.0-or-later", "MIT", "MIT-0", "MPL-1.1", "MPL-2.0", "MS-PL",
    "MS-RL", "NCSA", "OFL-1.1", "OpenSSL", "OSL-3.0", "PostgreSQL",
    "Python-2.0", "Sleepycat", "Unlicense", "UPL-1.0", "WTFPL", "Zlib",
    "ZPL-2.1",
})
_LICENSE_SPLIT_RE = re.compile(r"\b(AND|OR|WITH)\b")
_LICENSE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")


def validate_spdx_license(text: str) -> str:
    """Validate an SPDX license identifier/expression; return the normalized
    text or raise a typed fail-closed error. Empty/unknown/fake/malformed
    licenses and dangling/duplicate connectors are rejected. ``LicenseRef-*``
    and similar custom ids are unknown here and therefore rejected."""
    expr = (text or "").strip()
    if not expr:
        raise WebsiteIntegrationError("LICENSE_INVALID: empty license")
    if len(expr) > 200:
        raise WebsiteIntegrationError("LICENSE_INVALID: license too long")
    if re.search(r"\b(AND|OR|WITH)\s+(AND|OR|WITH)\b", expr):
        raise WebsiteIntegrationError("LICENSE_INVALID: duplicate connector")
    if re.match(r"\s*(AND|OR|WITH)\b", expr) or \
            re.search(r"\b(AND|OR|WITH)\s*$", expr):
        raise WebsiteIntegrationError("LICENSE_INVALID: dangling connector")
    if expr.count("(") != expr.count(")"):
        raise WebsiteIntegrationError("LICENSE_INVALID: unbalanced parentheses")
    for raw in _LICENSE_SPLIT_RE.split(expr):
        tok = raw.strip()
        if not tok or tok in ("AND", "OR", "WITH"):
            continue
        tok = tok.strip("()")
        if tok.endswith("+"):
            tok = tok[:-1]
        if not _LICENSE_TOKEN_RE.match(tok):
            raise WebsiteIntegrationError(
                f"LICENSE_INVALID: malformed SPDX token {raw.strip()!r}")
        if tok not in SPDX_LICENSES:
            raise WebsiteIntegrationError(
                f"LICENSE_UNKNOWN: unrecognized SPDX identifier {tok!r}")
    return expr


def canonical_manifest_hash(manifest_dict: Dict[str, Any]) -> str:
    """Canonical artifact-hash rule (Packet 8E provenance).

    ``content_hash`` is the SHA-256 hex digest of the CANONICAL JSON encoding
    of the skill-manifest PAYLOAD: keys sorted, ``(",", ":")`` separators,
    ASCII/ensure_ascii, tuples serialized as arrays, str-enums as their values,
    and the ``content_hash`` field itself EXCLUDED from the hashed payload.
    This is deterministic and recomputable from the stored manifest, so any
    divergence (corruption, tampering, or an incorrect installer value) fails
    closed. A well-shaped hash value is never treated as proof of authenticity
    on its own.
    """
    def _clean(v: Any) -> Any:
        if isinstance(v, str):
            return v
        if hasattr(v, "value"):
            return v.value
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        return v

    payload = {k: v for k, v in manifest_dict.items() if k != "content_hash"}
    canonical = json.dumps(_clean(payload), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_manifest_hash(manifest_dict: Dict[str, Any], claimed: Any) -> bool:
    """True only when ``claimed`` is a 64-hex digest equal to the canonical
    artifact hash. A well-shaped but WRONG value returns False; a well-shaped
    value is never treated as proof of authenticity by itself."""
    if not isinstance(claimed, str) or not _SHA256_RE.match(claimed):
        return False
    return canonical_manifest_hash(manifest_dict) == claimed


def _scope_authorized(manifest_scopes: Tuple[str, ...],
                      proposed_scope: str) -> bool:
    """A website origin scope is authorized by the linked manifest only via the
    exact ``web:<origin>`` scope or an explicitly permitted ``web`` scope."""
    ms = tuple(manifest_scopes or ())
    if proposed_scope in ms:
        return True
    if proposed_scope.startswith("web:") and "web" in ms:
        return True
    return False

_COMPAT_ALL = frozenset({"", "all", "any", "any-os", "cross-platform",
                         "portable", "linux", "linux/unix", "linux-unix",
                         "posix", "unix", "unix/linux", "linux64", "linux x86_64"})
_COMPAT_LINUX_TOKENS = ("linux", "unix", "posix")
_COMPAT_OTHER_OS = ("windows", "win32", "win64", "nt", "macos", "darwin",
                    "osx", "mac", "bsd", "solaris")

_PRIVATE_BLOCK_REASONS = (
    "localhost", "127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.",
    "172.19.", "172.2", "172.3", "0.0.0.0", "169.254.",
)
_PRIVATE_HOST_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")

_SECRET_QUERY_KEY = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|authorization|"
    r"credential|session[_-]?id|signature|sig)")

_KNOWN_AUTH_HOSTS = frozenset({
    "mail.google.com", "accounts.google.com", "facebook.com",
    "www.facebook.com", "instagram.com", "www.instagram.com",
    "messenger.com", "www.messenger.com", "x.com", "twitter.com",
    "www.twitter.com", "linkedin.com", "www.linkedin.com",
})

_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


class WebsiteIntegrationError(Exception):
    """Typed failure for the website-integration layer."""


class WebsiteValidationError(WebsiteIntegrationError):
    """A URL/record failed validation and was NOT persisted."""


class ConfirmationRequiredError(WebsiteIntegrationError):
    """The mutation needs explicit user confirmation before it may persist."""


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _bound(text: Optional[str], limit: int) -> str:
    return (text or "")[:limit]


# ---------------------------------------------------------------------------
# URL / origin validation (fail closed)
# ---------------------------------------------------------------------------
def normalize_origin(url: str, *, resolve: bool = True) -> Tuple[str, str, str]:
    """Return ``(origin, clean_url, name_hint)`` or raise.

    Origin is ``scheme://host[:port]`` (lower-cased host, no credentials, no
    query/fragment). The clean URL keeps scheme/host/path but drops query
    params and fragment. Query-token URLs are rejected outright.

    ``resolve=True`` (default) also rejects hostnames that resolve to private
    addresses (SSRF block). Preview-only paths pass ``resolve=False`` so a
    ``--dry-run`` performs no DNS lookup and no network call while still
    rejecting literal private/loopback origins.
    """
    raw = (url or "").strip()
    if not raw:
        raise WebsiteValidationError("empty URL")
    if len(raw) > 2048:
        raise WebsiteValidationError("URL too long")
    try:
        parts = urlsplit(raw)
        if parts.scheme.lower() not in ("http", "https"):
            raise WebsiteValidationError(
                f"invalid scheme {parts.scheme!r} (only http/https)")
        if parts.username or parts.password:
            raise WebsiteValidationError("credential-bearing URLs are rejected")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise WebsiteValidationError("missing host")
        if ":" in host:
            # IPv6 literal: must parse; private/reserved literals are rejected
            # later by blocked_host_reason.
            try:
                ipaddress.ip_address(host)
            except ValueError as exc:
                raise WebsiteValidationError(
                    f"malformed URL: {exc}") from None
        elif not _HOSTNAME_RE.match(host):
            raise WebsiteValidationError(
                "invalid hostname (whitespace or disallowed characters)")
        for key, _val in _split_qs(parts.query):
            if _SECRET_QUERY_KEY.search(key):
                raise WebsiteValidationError(
                    f"query-token URLs are rejected ({redact_text(key)})")
        port = ""
        if parts.port is not None:
            if parts.port < 1 or parts.port > 65535:
                raise WebsiteValidationError("invalid port")
            port = f":{parts.port}"
        origin = f"{parts.scheme.lower()}://{host}{port}"
        path = (parts.path or "")[:1024]
        clean_url = origin + path
        name_hint = _bound(host, MAX_DISPLAY_NAME)
    except ValueError as exc:
        raise WebsiteValidationError(f"malformed URL: {exc}") from None
    reason = blocked_host_reason(host, resolve=resolve)
    if reason == "PRIVATE_NETWORK":
        raise WebsiteValidationError("private/out-of-scope origins are rejected")
    return origin, clean_url, name_hint


def _split_qs(query: str) -> List[Tuple[str, str]]:
    out = []
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out.append((k, v))
        elif kv:
            out.append((kv, ""))
    return out


def blocked_host_reason(host: str, *, resolve: bool = True) -> Optional[str]:
    """Return a reason string when ``host`` is private/out-of-scope, else None.

    ``resolve=False`` skips the DNS resolution step entirely (literal private
    IPs, loopback, link-local, reserved literals and known private host
    suffixes are still rejected). It is used by preview-only paths so a
    ``--dry-run`` never performs a DNS lookup. ``resolve=True`` (default) also
    rejects any hostname that resolves to a private address (SSRF block).
    """
    low = (host or "").lower().rstrip(".")
    if not low:
        return "EMPTY_HOST"
    if low == "localhost" or low.endswith(_PRIVATE_HOST_SUFFIXES):
        return "PRIVATE_NETWORK"
    try:
        ip_obj = ipaddress.ip_address(low)
    except ValueError:
        pass
    else:
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local \
                or ip_obj.is_reserved or ip_obj.is_multicast \
                or ip_obj.is_unspecified:
            return "PRIVATE_NETWORK"
        return None
    if not resolve:
        return None   # preview path: no DNS, no network
    # Literal hostname: resolve once and reject if ANY address is private
    # (fail closed on mixed/rebinding DNS).
    try:
        addrs = socket.getaddrinfo(low, None)
    except OSError:
        return None   # resolution failure is reported as UNREACHABLE by probe
    for family, _socktype, _proto, _cn, sockaddr in addrs:
        ip = sockaddr[0].split("%")[0]
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local \
                or ip_obj.is_reserved or ip_obj.is_multicast \
                or ip_obj.is_unspecified:
            return "PRIVATE_NETWORK"
    return None


# ---------------------------------------------------------------------------
# Bounded read-only discovery
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    origin: str
    title: str = ""
    reachable: bool = False
    auth_required: bool = False
    observation_hints: Tuple[str, ...] = ()
    adapter_requirement: str = ""
    reason_code: str = ""
    http_status: int = 0
    duration_s: float = 0.0
    bytes_read: int = 0
    limits: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = redact_obj(self.evidence)
        return d


def _extract_title(raw: bytes) -> str:
    """Bounded, untrusted-but-redacted <title> extraction (never policy)."""
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        return ""
    m = _TITLE_RE.search(text)
    if not m:
        return ""
    title = _TAG_RE.sub("", m.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    return _bound(redact_text(title), MAX_TITLE)


def probe_page(url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
               max_bytes: int = DEFAULT_MAX_BYTES,
               max_redirects: int = DEFAULT_MAX_REDIRECTS,
               clock: Callable[[], float] = time.time) -> Dict[str, Any]:
    """Default bounded read-only HTTP probe (injectable in tests).

    Never sends cookies/authorization, never executes page JavaScript, reads
    at most ``max_bytes``, follows at most ``max_redirects`` redirects with
    per-hop SSRF/scheme/credential re-checks, and returns only redacted
    metadata. Any private-network target fails closed as DENIED.
    """
    started = clock()
    result = {"status": STATUS_FAILED, "origin": "", "title": "",
              "reachable": False, "auth_required": False,
              "observation_hints": (), "adapter_requirement": "",
              "reason_code": "UNREACHABLE", "http_status": 0,
              "duration_s": 0.0, "bytes_read": 0,
              "limits": {"timeout_s": timeout_s, "max_bytes": max_bytes,
                         "max_redirects": max_redirects},
              "evidence": {}}

    def _finish(status, reason, **kw) -> Dict[str, Any]:
        result.update(status=status, reason_code=reason,
                      duration_s=max(0.0, clock() - started))
        result.update(kw)
        result["evidence"] = {"origin": result["origin"],
                              "title": result["title"],
                              "http_status": result["http_status"],
                              "reason": result["reason_code"],
                              "hints": list(result["observation_hints"])}
        return dict(result)

    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    reason = blocked_host_reason(host)
    if reason:
        return _finish(STATUS_DENIED, reason,
                       origin=f"{parts.scheme}://{host}")
    current = url
    headers = {"User-Agent": "QubitVoiceAgent-Discovery/1.0 (bounded, read-only)",
               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.6"}
    status = 0
    final_url = current
    body = b""
    ctype = ""
    for _ in range(max_redirects + 1):
        try:
            cur = urlsplit(current)
        except ValueError:
            return _finish(STATUS_DEGRADED, "MALFORMED_REDIRECT",
                           origin=origin, http_status=0,
                           observation_hints=("malformed_redirect",))
        c_reason = blocked_host_reason(cur.hostname or "")
        if c_reason:
            return _finish(STATUS_DENIED, c_reason,
                           origin=f"{cur.scheme}://{(cur.hostname or '').lower()}")
        if cur.scheme not in ("http", "https"):
            return _finish(STATUS_DENIED, "INVALID_SCHEME",
                           origin=f"{cur.scheme}://{(cur.hostname or '').lower()}")
        if cur.username or cur.password:
            return _finish(STATUS_DENIED, "CREDENTIALS",
                           origin=f"{cur.scheme}://{(cur.hostname or '').lower()}")
        try:
            req = Request(current, headers=headers, method="GET")
            with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                status = resp.getcode() or 0
                final_url = resp.geturl()
                ctype = resp.headers.get("Content-Type", "") or ""
                body = resp.read(max_bytes + 1)
                if len(body) > max_bytes:
                    body = body[:max_bytes]
                if status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location")
                    if not loc:
                        return _finish(STATUS_DEGRADED, "NO_REDIRECT_TARGET",
                                       http_status=status)
                    current = urljoin(current, loc)
                    continue
                break
        except HTTPError as exc:
            status = exc.code
            final_url = current
            break
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            return _finish(STATUS_FAILED,
                           "TLS_ERROR" if "ssl" in str(reason).lower() else "UNREACHABLE")
        except (TimeoutError, socket.timeout, OSError):
            return _finish(STATUS_DEGRADED, "TIMEOUT", http_status=0)
        except Exception:
            return _finish(STATUS_FAILED, "PROBE_ERROR", http_status=0)

    cur = urlsplit(final_url)
    origin = f"{cur.scheme}://{(cur.hostname or '').lower()}" \
        + (f":{cur.port}" if cur.port else "")
    host = (cur.hostname or "").lower().rstrip(".")
    if status in (301, 302, 303, 307, 308):
        # redirect limit hit (loop or chain longer than max_redirects)
        return _finish(STATUS_DEGRADED, "TOO_MANY_REDIRECTS",
                       origin=origin, http_status=status,
                       observation_hints=("redirect_limit",),
                       reachable=True)
    if status in (401, 403) or host in _KNOWN_AUTH_HOSTS \
            or host.endswith(".facebook.com") or host.endswith(".instagram.com") \
            or host.endswith(".messenger.com") or host.endswith(".x.com"):
        return _finish(STATUS_AUTH_REQUIRED, "AUTH_REQUIRED",
                       origin=origin, auth_required=True,
                       http_status=status,
                       adapter_requirement="authenticated_cdp_adapter_required",
                       observation_hints=("auth_required",),
                       reachable=True)
    if status >= 400:
        return _finish(STATUS_DEGRADED, f"HTTP_ERROR_{status}",
                       origin=origin, http_status=status,
                       observation_hints=("http_error",))
    if not body:
        return _finish(STATUS_DEGRADED, "NO_DATA",
                       origin=origin, http_status=status,
                       observation_hints=("empty",))
    if not content_type_supported(ctype):
        return _finish(STATUS_DEGRADED, "CONTENT_TYPE_UNSUPPORTED",
                       origin=origin, http_status=status,
                       observation_hints=("unsupported_content_type",),
                       reachable=True)
    title = _extract_title(body)
    if not title:
        return _finish(STATUS_SELECTOR_DRIFT, "NO_METADATA",
                       origin=origin, http_status=status,
                       observation_hints=("reachable",),
                       adapter_requirement="selector_review_required",
                       reachable=True)
    return _finish(STATUS_READY, "OK", origin=origin, title=title,
                   http_status=status, reachable=True,
                   observation_hints=("http_get", "title_metadata"),
                   adapter_requirement="none",
                   bytes_read=len(body))


def discover(record_or_url: Any, *,
             probe: Callable[[str], Dict[str, Any]] = probe_page,
             clock: Callable[[], float] = time.time) -> DiscoveryResult:
    """Run the injectable probe and map it to a typed DiscoveryResult.

    Only a READY static page produces a read-only capability proposal; every
    other outcome proposes nothing (fail closed). Page text/metadata are
    untrusted evidence and can never widen permissions or change policy.
    """
    url = getattr(record_or_url, "url", None) or record_or_url
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{(parts.hostname or '').lower()}" \
        + (f":{parts.port}" if parts.port else "")
    raw = probe(url)
    if not isinstance(raw, dict):
        raw = {"status": STATUS_FAILED, "reason_code": "PROBE_INVALID"}
    status = str(raw.get("status") or STATUS_FAILED)
    if status not in STATUSES:
        status = STATUS_FAILED
    result = DiscoveryResult(
        status=status,
        origin=str(raw.get("origin") or origin),
        title=_bound(redact_text(str(raw.get("title") or "")), MAX_TITLE),
        reachable=bool(raw.get("reachable")),
        auth_required=bool(raw.get("auth_required")),
        observation_hints=tuple(
            str(h) for h in (raw.get("observation_hints") or ())[:MAX_HINTS]),
        adapter_requirement=_bound(str(raw.get("adapter_requirement") or ""), 80),
        reason_code=_bound(str(raw.get("reason_code") or ""), MAX_REASON),
        http_status=int(raw.get("http_status") or 0),
        duration_s=max(0.0, float(raw.get("duration_s") or 0.0)),
        bytes_read=int(raw.get("bytes_read") or 0),
        limits=dict(raw.get("limits") or {}),
        evidence=dict(raw.get("evidence") or {}),
    )
    return result


def proposed_for(result: DiscoveryResult) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Capability proposal derived ONLY from the typed discovery result.

    READY static pages get the read-only observation capability set scoped to
    their origin; every other lifecycle state proposes nothing.
    """
    if result.status != STATUS_READY or not result.reachable:
        return (), ()
    return tuple(OBSERVATION_CAPABILITIES), (f"web:{result.origin}",)


def add_preview(url: str, name: str = "") -> Dict[str, Any]:
    """Validate a URL and return a preview (no persistence, no network).

    Side-effect-free by contract: no DNS lookup, no network call, no state
    write, no backup, no audit record, no grant, no skill, no page execution.
    Private/loopback literal origins are still rejected without resolving.
    """
    origin, clean_url, name_hint = normalize_origin(url, resolve=False)
    return {"state": "preview", "would_add": origin, "url": clean_url,
            "name": name.strip() or name_hint, "status": STATUS_PENDING,
            "granted": False}


def validate_probe_result(raw: Any) -> Dict[str, Any]:
    """Validate an operator-supplied synthetic probe result (fixture).

    The fixture only controls the discovery OUTCOME (status/title/reachability/
    auth/hints); the capability/scope proposal is ALWAYS derived by
    ``proposed_for`` from the typed status, never from the fixture. Fields are
    bounded and the title is redacted. Anything malformed fails closed.
    """
    if not isinstance(raw, dict):
        raise WebsiteValidationError("fixture must be a JSON object")
    status = str(raw.get("status") or STATUS_FAILED)
    if status not in STATUSES:
        raise WebsiteValidationError(f"fixture has invalid status {status!r}")
    title = _bound(redact_text(str(raw.get("title") or "")), MAX_TITLE)
    hints = tuple(str(h) for h in (raw.get("observation_hints") or ())[:MAX_HINTS])
    try:
        http_status = int(raw.get("http_status") or 0)
        duration_s = max(0.0, float(raw.get("duration_s") or 0.0))
        bytes_read = int(raw.get("bytes_read") or 0)
    except (TypeError, ValueError) as exc:
        raise WebsiteValidationError(
            f"fixture has a non-numeric field: {exc}") from None
    return {
        "status": status,
        "origin": "",
        "title": title,
        "reachable": bool(raw.get("reachable")),
        "auth_required": bool(raw.get("auth_required")),
        "observation_hints": hints,
        "adapter_requirement": _bound(str(raw.get("adapter_requirement") or ""), 80),
        "reason_code": _bound(str(raw.get("reason_code") or ""), MAX_REASON),
        "http_status": http_status,
        "duration_s": duration_s,
        "bytes_read": bytes_read,
        "limits": dict(raw.get("limits") or {}),
        "evidence": {"fixture": True},
    }


def content_type_supported(ctype: str) -> bool:
    """True when a response content-type is HTML-ish (probeable).

    The bounded live probe only reads safe metadata from HTML/text pages;
    binary or unsupported content types are reported as a typed DEGRADED
    result instead of being parsed. An absent content type is allowed (some
    servers omit it) but the body is still bounded.
    """
    if not ctype or not ctype.strip():
        return True
    low = ctype.strip().lower()
    return low.startswith("text/html") or low.startswith("text/plain") \
        or low.startswith("application/xhtml+xml")


def probe_url(url: str, *, probe: Callable[[str], Dict[str, Any]] = probe_page,
              clock: Callable[[], float] = time.time,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              max_bytes: int = DEFAULT_MAX_BYTES,
              max_redirects: int = DEFAULT_MAX_REDIRECTS) -> DiscoveryResult:
    """Safe, bounded live probe of a public non-authenticated HTTPS URL.

    Operator must explicitly opt in to live network access (``--live``). Only
    PUBLIC HTTPS origins are permitted (no http, no credentials, no
    query-token, no private networks, no cookies, no accounts). Runs the
    injectable bounded probe (timeout/size/redirect/content-type/origin
    limits), returns a redacted typed DiscoveryResult and PERSISTS NOTHING. A
    ``probe`` returning anything but a typed status fails closed to FAILED; a
    probe that itself raises fails closed to FAILED/``PROBE_UNAVAILABLE``.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise WebsiteValidationError(
            "live probe permits only public HTTPS sites (no http)")
    if parts.fragment:
        raise WebsiteValidationError(
            "live probe rejects URLs with fragments")
    origin, _clean_url, _name = normalize_origin(url)
    try:
        result = discover(url, probe=probe, clock=clock)
    except Exception:
        result = DiscoveryResult(
            status=STATUS_FAILED, origin=origin, reason_code="PROBE_UNAVAILABLE",
            adapter_requirement="", limits={"timeout_s": timeout_s,
                                            "max_bytes": max_bytes,
                                            "max_redirects": max_redirects},
            evidence={"reason": "PROBE_UNAVAILABLE"})
    return result


def result_code_for_status(status: str, reason: str = "") -> str:
    """Map a discovery status to the probe result vocabulary.

    SUCCESS / UNAVAILABLE / DEGRADED / PARTIAL / AUTH_REQUIRED / FAILED.
    A FAILED result whose reason is unreachable/timeout/offline reads as
    UNAVAILABLE (the site could not be reached); everything else is FAILED.
    """
    status = str(status)
    if status == STATUS_READY:
        return "SUCCESS"
    if status == STATUS_AUTH_REQUIRED:
        return "AUTH_REQUIRED"
    if status == STATUS_SELECTOR_DRIFT:
        return "PARTIAL"
    if status == STATUS_DEGRADED:
        return "DEGRADED"
    if status == STATUS_DENIED:
        return "FAILED"
    if status == STATUS_FAILED:
        reason = str(reason or "")
        if reason in ("UNREACHABLE", "TIMEOUT", "NETWORK_DISABLED",
                      "PROBE_UNAVAILABLE"):
            return "UNAVAILABLE"
        return "FAILED"
    return "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Integration record
# ---------------------------------------------------------------------------
@dataclass
class WebsiteIntegration:
    integration_id: str
    origin: str
    url: str
    display_name: str
    status: str = STATUS_PENDING
    enabled: bool = False
    requested_capabilities: Tuple[str, ...] = ()
    requested_scopes: Tuple[str, ...] = ()
    grant_ids: Tuple[str, ...] = ()
    sensitivity: str = "personal"
    approval_policy: str = "auto"
    auth_required: bool = False
    manifest_link: str = ""
    source_url: str = ""
    source_revision: str = ""
    license: str = ""
    content_hash: str = ""
    compatibility: str = ""
    artifact_path: str = ""      # local artifact path (stored, NEVER serialized)
    artifact_sha256: str = ""   # optional: sha256 of actual local artifact bytes
    limits: Dict[str, float] = field(default_factory=dict)
    health: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    discovered_at: Optional[float] = None
    rollback_to: Optional[str] = None
    removed_at: Optional[float] = None
    removed_reason: str = ""

    def to_dict(self, *, redact_artifact: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        d["requested_capabilities"] = list(self.requested_capabilities)
        d["requested_scopes"] = list(self.requested_scopes)
        d["grant_ids"] = list(self.grant_ids)
        d["health"] = redact_obj(self.health)
        # the local artifact path is provenance for the GATE only. It is stored
        # verbatim in private state (redact_artifact=False) but NEVER serialized
        # to records, logs, dashboard, reports, or test output.
        if redact_artifact and self.artifact_path:
            d["artifact_path"] = "[local artifact]"
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WebsiteIntegration":
        return cls(
            integration_id=str(d["integration_id"]),
            origin=str(d["origin"]),
            url=str(d.get("url", "")),
            display_name=str(d.get("display_name", "")),
            status=str(d.get("status", STATUS_PENDING)),
            enabled=bool(d.get("enabled")),
            requested_capabilities=tuple(d.get("requested_capabilities", ())),
            requested_scopes=tuple(d.get("requested_scopes", ())),
            grant_ids=tuple(d.get("grant_ids", ())),
            sensitivity=str(d.get("sensitivity", "personal")),
            approval_policy=str(d.get("approval_policy", "auto")),
            auth_required=bool(d.get("auth_required")),
            manifest_link=str(d.get("manifest_link", "")),
            source_url=str(d.get("source_url", "")),
            source_revision=str(d.get("source_revision", "")),
            license=str(d.get("license", "")),
            content_hash=str(d.get("content_hash", "")),
            compatibility=str(d.get("compatibility", "")),
            artifact_path=str(d.get("artifact_path", "")),
            artifact_sha256=str(d.get("artifact_sha256", "")),
            limits=dict(d.get("limits", {})),
            health=dict(d.get("health", {})),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
            discovered_at=d.get("discovered_at"),
            rollback_to=d.get("rollback_to"),
            removed_at=d.get("removed_at"),
            removed_reason=str(d.get("removed_reason", "")),
        )

    def with_fields(self, **kw: Any) -> "WebsiteIntegration":
        data = asdict(self)
        data.update(kw)
        return WebsiteIntegration.from_dict(data)


# ---------------------------------------------------------------------------
# Durable store (atomic + audited, private modes)
# ---------------------------------------------------------------------------
class WebsiteIntegrationStore:
    """Persistent, atomic, audited integration-record store.

    State is a private JSON file with timestamped backups and a bounded
    redacted audit log, mirroring ``PermissionsStore``. Adding a URL NEVER
    grants anything: records are PENDING and disabled until inspected and
    explicitly enabled/granted.
    """

    def __init__(self, path: Path | str, *,
                 private_files: bool = True) -> None:
        self._path = Path(path)
        self._private_files = bool(private_files)
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "integrations": {},
            "removed": {},
            "audit": [],
        }
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text())
                if isinstance(loaded, dict):
                    self._state = loaded
            except Exception:
                pass   # corrupt file -> fail closed to empty defaults
        if self._private_files:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass

    # ------------------------------------------------------------ accessors
    @property
    def path(self) -> Path:
        return self._path

    def integrations(self) -> List[WebsiteIntegration]:
        with self._lock:
            recs = [WebsiteIntegration.from_dict(r)
                    for r in self._state.get("integrations", {}).values()]
        return sorted(recs, key=lambda r: (r.created_at, r.integration_id))

    def removed(self) -> List[WebsiteIntegration]:
        with self._lock:
            recs = [WebsiteIntegration.from_dict(r)
                    for r in self._state.get("removed", {}).values()]
        return sorted(recs, key=lambda r: (r.removed_at or 0, r.integration_id))

    def lookup(self, identifier: str) -> Optional[WebsiteIntegration]:
        for r in self.integrations():
            if identifier in (r.integration_id, r.origin, r.url):
                return r
        return None

    def audit(self, limit: int = AUDIT_CAP) -> List[Dict[str, Any]]:
        with self._lock:
            return [redact_obj(e) for e in
                    list(self._state.get("audit", []))[-int(limit):]]

    def logs(self, identifier: str, tail: int = 50) -> List[Dict[str, Any]]:
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        with self._lock:
            rows = [e for e in self._state.get("audit", [])
                    if e.get("integration_id") == rec.integration_id]
        return [redact_obj(e) for e in rows[-int(tail):]]

    # ------------------------------------------------------------ persistence
    def _persist_integration(self, rec: WebsiteIntegration, *,
                             removed: bool = False) -> None:
        """Persist a record into private state with the artifact path VERBATIM
        (never redacted) so the gate can re-verify bytes later."""
        key = "removed" if removed else "integrations"
        self._state.setdefault(key, {})[rec.integration_id] = \
            rec.to_dict(redact_artifact=False)

    def _backup(self) -> Optional[Path]:
        try:
            bak = self._path.with_name(
                f"{self._path.name}.{int(_now())}.bak")
            bak.write_text(json.dumps(self._state, indent=2))
            if self._private_files:
                os.chmod(bak, 0o600)
            return bak
        except OSError:
            return None

    def _save(self) -> Path:
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                   prefix=f".{self._path.name}.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._state, fh, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self._private_files:
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        return self._path

    def _audit(self, op: str, rec: WebsiteIntegration, *, reason: str = "",
               duration_s: float = 0.0, counts: Optional[Dict[str, int]] = None,
               evidence: str = "") -> None:
        entry = {
            "ts": _now(),
            "integration_id": rec.integration_id,
            "origin": rec.origin,
            "op": op,
            "status": rec.status,
            "reason": _bound(reason, MAX_REASON),
            "duration_s": round(max(0.0, duration_s), 4),
            "counts": counts or {},
            "revision": _bound(rec.source_revision, 64),
            "evidence": _bound(evidence or (
                "available" if rec.health else "none"), 32),
        }
        entry = redact_obj(entry)
        with self._lock:
            self._state.setdefault("audit", []).append(entry)
            if len(self._state["audit"]) > AUDIT_CAP:
                del self._state["audit"][:len(self._state["audit"]) - AUDIT_CAP]
        log_event("websites." + op, integration_id=rec.integration_id,
                  origin=rec.origin, status=rec.status, reason=entry["reason"],
                  duration_s=entry["duration_s"], counts=entry["counts"])

    # ------------------------------------------------------------ lifecycle
    def add(self, url: str, *, requested_by: str,
            display_name: str = "") -> WebsiteIntegration:
        """Validate + persist a PENDING, disabled proposal. Grants NOTHING."""
        origin, clean_url, name_hint = normalize_origin(url)
        if any(i.origin == origin for i in self.integrations()):
            raise WebsiteIntegrationError(f"origin already tracked: {origin}")
        name = _bound(display_name.strip() or name_hint, MAX_DISPLAY_NAME)
        now = _now()
        rec = WebsiteIntegration(
            integration_id=_new_id("ws"), origin=origin, url=clean_url,
            display_name=name, status=STATUS_PENDING, enabled=False,
            requested_capabilities=(), requested_scopes=(),
            grant_ids=(), sensitivity="personal", approval_policy="auto",
            auth_required=False, manifest_link="", source_revision="",
            license="", content_hash="",
            limits={"timeout_s": DEFAULT_TIMEOUT_S,
                    "max_bytes": DEFAULT_MAX_BYTES,
                    "max_redirects": DEFAULT_MAX_REDIRECTS},
            health={}, created_at=now, updated_at=now)
        with self._lock:
            self._backup()
            self._persist_integration(rec)
        self._audit("add", rec, reason="PENDING")
        with self._lock:
            self._save()
        return rec

    def run_discovery(self, identifier: str, *, requested_by: str,
                      probe: Optional[Callable[[str], Dict[str, Any]]] = None,
                      clock: Callable[[], float] = time.time,
                      probe_source: str = "live") -> WebsiteIntegration:
        """Run bounded read-only discovery and persist the typed result.

        ``probe_source`` records where the probe came from ("live" network,
        "fixture" operator-synthetic, or "offline") in health and audit so a
        synthetic result is never mistaken for a live observation.
        """
        with self._lock:
            rec = self.lookup(identifier)
            if rec is None:
                raise WebsiteIntegrationError(
                    f"unknown integration {identifier!r}")
        if rec.status == STATUS_REMOVED:
            raise WebsiteIntegrationError("integration is removed")
        started = clock()
        result = discover(rec, probe=probe or probe_page, clock=clock)
        caps, scopes = proposed_for(result)
        now = clock()
        new = rec.with_fields(
            status=result.status, auth_required=result.auth_required,
            requested_capabilities=caps, requested_scopes=scopes,
            health={"reachable": result.reachable,
                    "title": result.title,
                    "reason_code": result.reason_code,
                    "http_status": result.http_status,
                    "observation_hints": list(result.observation_hints),
                    "adapter_requirement": result.adapter_requirement,
                    "duration_s": round(result.duration_s, 4),
                    "bytes_read": result.bytes_read,
                    "probe": _bound(probe_source, 32),
                    "observed_at": now,
                    "limits": result.limits},
            discovered_at=now, updated_at=now,
            rollback_to=rec.status if rec.status != result.status else None)
        with self._lock:
            self._backup()
            self._persist_integration(new)
        self._audit("discover", new, reason=result.reason_code,
                    duration_s=result.duration_s,
                    counts={"capabilities": len(caps), "scopes": len(scopes),
                            "probe": probe_source},
                    evidence="available" if result.reachable else "none")
        with self._lock:
            self._save()
        return new

    def enable(self, identifier: str, *, requested_by: str, confirmed: bool,
               perms: Optional[Any] = None) -> WebsiteIntegration:
        if not confirmed:
            raise ConfirmationRequiredError(
                "website enable requires explicit confirmation")
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        if rec.status in _NO_PROPOSAL or rec.status == STATUS_REMOVED:
            raise WebsiteIntegrationError(
                f"cannot enable a {rec.status} integration")
        if not rec.requested_capabilities:
            raise WebsiteIntegrationError(
                "no validated proposal yet (run inspect first)")
        self._check_linked_and_bound(rec, perms)
        new = rec.with_fields(status=STATUS_READY, enabled=True,
                              rollback_to=rec.status if rec.status
                              not in (STATUS_READY, STATUS_DISABLED) else None,
                              updated_at=_now())
        with self._lock:
            self._backup()
            self._persist_integration(new)
        self._audit("enable", new, reason="READY")
        with self._lock:
            self._save()
        return new

    def disable(self, identifier: str, *, requested_by: str,
                confirmed: bool) -> WebsiteIntegration:
        if not confirmed:
            raise ConfirmationRequiredError(
                "website disable requires explicit confirmation")
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        new = rec.with_fields(status=STATUS_DISABLED, enabled=False,
                              rollback_to=rec.status, updated_at=_now())
        with self._lock:
            self._backup()
            self._persist_integration(new)
        self._audit("disable", new, reason="DISABLED")
        with self._lock:
            self._save()
        return new

    # ------------------------------------------------------------ manifest link
    def _verify_artifact(self, rec: WebsiteIntegration) -> None:
        """Re-verify the linked local artifact bytes at EVERY use gate.

        If the record claims artifact verification, the recorded artifact file
        must still exist AND still hash to the recorded digest. A moved,
        deleted, or modified artifact fails closed (typed MISSING / TAMPERED /
        UNREADABLE). Reads bytes only — never executes package hooks, installers,
        arbitrary code, or downloaded content. No artifact claim -> no check.
        """
        if not rec.artifact_path and not rec.artifact_sha256:
            return
        if not rec.artifact_path:
            raise WebsiteIntegrationError(
                "SKILL_ARTIFACT_MISSING: artifact verified at link time but no "
                "artifact path is recorded (re-verification impossible)")
        apath = Path(rec.artifact_path)
        if not apath.is_file():
            raise WebsiteIntegrationError(
                f"SKILL_ARTIFACT_MISSING: linked artifact file is gone "
                f"({apath.name}) — moved or deleted after linking")
        try:
            actual = hashlib.sha256(apath.read_bytes()).hexdigest()
        except OSError as exc:
            raise WebsiteIntegrationError(
                f"SKILL_ARTIFACT_UNREADABLE: cannot re-read artifact bytes: "
                f"{exc}") from None
        if actual != rec.artifact_sha256:
            raise WebsiteIntegrationError(
                "SKILL_ARTIFACT_TAMPERED: linked artifact bytes no longer match "
                "the verified digest (modified after linking)")

    def _artifact_status(self, rec: WebsiteIntegration) -> str:
        """Read-only artifact re-verification status for check output."""
        if not rec.artifact_path and not rec.artifact_sha256:
            return "not_applicable"
        try:
            self._verify_artifact(rec)
        except WebsiteIntegrationError as exc:
            detail = str(exc)
            if detail.startswith("SKILL_ARTIFACT_MISSING"):
                return "missing"
            if detail.startswith("SKILL_ARTIFACT_TAMPERED"):
                return "tampered"
            return "unreadable"
        return "verified"

    def _check_linked_and_bound(self, rec: WebsiteIntegration,
                                perms: Any) -> Any:
        """Shared fail-closed gate for enable AND grant.

        Verifies, in order: permission store present -> a link exists -> the
        linked skill is present/active/SPDX-licensed/hash-verified/compatible ->
        the manifest authorizes the proposal (capability/scope/tier/sensitivity/
        approval binding). ``PermissionsStore.grant`` is only ever reached AFTER
        this gate passes.
        """
        if perms is None:
            raise WebsiteIntegrationError("permission store is required")
        if not rec.manifest_link:
            raise WebsiteIntegrationError(
                "no linked 7E manifest (run websites manifest-link first)")
        skill = validate_manifest_link(rec.manifest_link, perms)
        validate_website_binding(rec, skill)
        self._verify_artifact(rec)
        return skill

    def link_manifest(self, identifier: str, *, skill_id: str,
                      requested_by: str, confirmed: bool,
                      perms: Optional[Any] = None,
                      artifact_path: Optional[str] = None,
                      artifact_sha256: Optional[str] = None) -> WebsiteIntegration:
        """Explicitly link a valid, installed, active 7E skill manifest.

        This is the ONLY writer of ``manifest_link``. The record stays
        unlinked until the user performs this explicit, confirmed action; the
        linked skill must pass validate_manifest_link (present, re-validated,
        active, SPDX-licensed, hash-verified against the canonical artifact,
        compatible). When a proposal already exists, the capability/scope/tier/
        sensitivity/approval binding is also enforced here. Provenance fields
        (source URL, revision, license, content hash, compatibility) are copied
        onto the record. Linking grants/enables nothing.

        OPTIONAL artifact-byte verification: if ``artifact_path`` (a local,
        operator-supplied file) AND ``artifact_sha256`` are given, the file
        must exist, be readable, and its bytes must hash to ``artifact_sha256``
        (computed locally with no hooks/installers/network). The verified
        digest is recorded as provenance. If no artifact is supplied, no
        artifact claim is made or implied.
        """
        if not confirmed:
            raise ConfirmationRequiredError(
                "website manifest-link requires explicit confirmation")
        if perms is None:
            raise WebsiteIntegrationError("permission store is required")
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        if rec.status == STATUS_REMOVED:
            raise WebsiteIntegrationError("integration is removed")
        skill = validate_manifest_link(skill_id, perms)
        # Binding is enforced here whenever a proposal already exists; otherwise
        # it is deferred to the shared enable/grant gate (fail closed at use).
        if rec.requested_capabilities and rec.requested_scopes:
            validate_website_binding(rec, skill)
        artifact_verified = ""
        artifact_store = ""
        if artifact_path or artifact_sha256:
            if not artifact_path or not artifact_sha256:
                raise WebsiteIntegrationError(
                    "SKILL_ARTIFACT_MISSING: artifact-byte verification "
                    "requires BOTH --artifact PATH and --artifact-sha256 HEX")
            if not _SHA256_RE.match(str(artifact_sha256)):
                raise WebsiteIntegrationError(
                    "SKILL_ARTIFACT_HASH_INVALID: artifact_sha256 must be a "
                    "64-char sha256 hex digest")
            apath = Path(str(artifact_path)).expanduser()
            if not apath.is_file():
                raise WebsiteIntegrationError(
                    f"SKILL_ARTIFACT_MISSING: artifact file not found: "
                    f"{apath.name} (local artifact bytes cannot be verified)")
            try:
                actual = hashlib.sha256(apath.read_bytes()).hexdigest()
            except OSError as exc:
                raise WebsiteIntegrationError(
                    f"SKILL_ARTIFACT_UNREADABLE: cannot read artifact bytes: "
                    f"{exc}") from None
            if actual != str(artifact_sha256).lower():
                raise WebsiteIntegrationError(
                    f"SKILL_ARTIFACT_HASH_MISMATCH: artifact bytes hash to "
                    f"{actual[:16]}…, not the supplied {str(artifact_sha256)[:16]}…")
            artifact_verified = str(artifact_sha256).lower()
            artifact_store = str(apath.resolve())
        new = rec.with_fields(
            manifest_link=skill.skill_id,
            source_url=skill.manifest.source,
            source_revision=skill.manifest.revision,
            license=skill.manifest.license,
            content_hash=skill.manifest.content_hash,
            compatibility=skill.manifest.compatibility,
            artifact_path=artifact_store,
            artifact_sha256=artifact_verified,
            updated_at=_now())
        with self._lock:
            self._backup()
            self._persist_integration(new)
        self._audit("manifest-link", new, reason="LINKED",
                    counts={"capabilities": len(skill.manifest.capabilities),
                            "scopes": len(skill.manifest.scopes),
                            "artifact_verified": 1 if artifact_verified else 0})
        with self._lock:
            self._save()
        return new

    # ------------------------------------------------------------ grants
    def grant(self, identifier: str, *, requested_by: str, confirmed: bool,
              capabilities: Tuple[str, ...] = (),
              ttl_s: Optional[float] = None,
              perms: Optional[Any] = None) -> Dict[str, Any]:
        """Grant proposed read-only capabilities scoped to the origin.

        Every granted capability must already be in the validated proposal and
        the scope must be the integration's origin scope; an expiry is
        required. The shared fail-closed gate (_check_linked_and_bound) verifies
        the active linked manifest AND the capability/scope/tier/sensitivity/
        approval binding BEFORE PermissionsStore.grant is called. No
        page/model/tool text can widen this.
        """
        if not confirmed:
            raise ConfirmationRequiredError(
                "website grant requires explicit confirmation")
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        if rec.status != STATUS_READY or not rec.requested_capabilities:
            raise WebsiteIntegrationError(
                f"cannot grant on a {rec.status} integration without a proposal")
        self._check_linked_and_bound(rec, perms)
        if ttl_s is None:
            ttl_s = DEFAULT_GRANT_TTL_S
        try:
            ttl_s = float(ttl_s)
        except (TypeError, ValueError):
            raise WebsiteIntegrationError("invalid ttl_s") from None
        if not (1.0 <= ttl_s <= 86400 * 90):
            raise WebsiteIntegrationError("ttl_s outside [1s, 90d]")
        wanted = tuple(capabilities) or tuple(rec.requested_capabilities)
        unknown = tuple(c for c in wanted if c not in rec.requested_capabilities)
        if unknown:
            raise WebsiteIntegrationError(
                f"capabilities outside the validated proposal: {unknown}")
        scopes = tuple(rec.requested_scopes)
        if not scopes:
            raise WebsiteIntegrationError("proposal has no origin scope")
        for s in scopes:
            if not _SCOPE_RE.match(s):
                raise WebsiteIntegrationError(f"invalid scope {s!r}")
        made = []
        for cap in wanted:
            g = perms.grant(cap, requested_by=requested_by, confirmed=True,
                            scopes=scopes, ttl_s=ttl_s,
                            note=f"website integration {rec.integration_id} "
                                 f"({rec.origin})")
            made.append(g.to_dict())
        new = rec.with_fields(
            grant_ids=tuple(dict.fromkeys(tuple(rec.grant_ids) +
                                          tuple(g["id"] for g in made))),
            updated_at=_now())
        with self._lock:
            self._backup()
            self._persist_integration(new)
        self._audit("grant", new, reason="GRANTED",
                    counts={"grants": len(made), "capabilities": len(wanted)})
        with self._lock:
            self._save()
        return {"state": "OK", "grants": made, "integration": new.to_dict()}

    def revoke(self, identifier: str, *, requested_by: str, confirmed: bool,
               perms: Optional[Any] = None) -> Dict[str, Any]:
        if not confirmed:
            raise ConfirmationRequiredError(
                "website revoke requires explicit confirmation")
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        revoked = []
        if perms is not None:
            for gid in list(rec.grant_ids):
                try:
                    g = perms.revoke(gid, requested_by=requested_by,
                                     confirmed=True)
                    revoked.append(g.to_dict())
                except Exception:
                    continue   # already revoked; keep the record id list clean
        new = rec.with_fields(grant_ids=(), updated_at=_now())
        with self._lock:
            self._backup()
            self._persist_integration(new)
        self._audit("revoke", new, reason="REVOKED",
                    counts={"revoked": len(revoked)})
        with self._lock:
            self._save()
        return {"state": "OK", "revoked": revoked, "integration": new.to_dict()}

    # ------------------------------------------------------------ remove/rollback
    def remove(self, identifier: str, *, requested_by: str, confirmed: bool,
               perms: Optional[Any] = None) -> Dict[str, Any]:
        if not confirmed:
            raise ConfirmationRequiredError(
                "website remove requires explicit confirmation")
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        revoked = []
        if perms is not None:
            for gid in list(rec.grant_ids):
                try:
                    g = perms.revoke(gid, requested_by=requested_by,
                                     confirmed=True)
                    revoked.append(g.to_dict())
                except Exception:
                    continue
        now = _now()
        new = rec.with_fields(status=STATUS_REMOVED, enabled=False,
                              grant_ids=(), rollback_to=rec.status,
                              removed_at=now, removed_reason="user remove",
                              updated_at=now)
        with self._lock:
            self._backup()
            self._persist_integration(new, removed=True)
            self._state.setdefault("integrations", {}).pop(new.integration_id,
                                                           None)
        self._audit("remove", new, reason="REMOVED",
                    counts={"revoked": len(revoked)})
        with self._lock:
            self._save()
        return {"state": "OK", "removed": new.to_dict(), "revoked": revoked}

    def rollback(self, identifier: str, *, requested_by: str,
                 confirmed: bool) -> Dict[str, Any]:
        """Restore a removed record, or flip back to its prior status.

        Grants are NEVER auto-restored: re-granting is an explicit,
        confirmed, permission decision.
        """
        if not confirmed:
            raise ConfirmationRequiredError(
                "website rollback requires explicit confirmation")
        rec = self.lookup(identifier)
        if rec is None:
            with self._lock:
                removed = self._state.get("removed", {}).get(identifier)
                if removed is None:
                    raise WebsiteIntegrationError(
                        f"unknown integration {identifier!r}")
                rec = WebsiteIntegration.from_dict(removed)
        if rec.status == STATUS_REMOVED:
            restored = rec.with_fields(
                status=rec.rollback_to
                if rec.rollback_to in STATUSES and rec.rollback_to != STATUS_REMOVED
                else STATUS_PENDING,
                enabled=False, rollback_to=None,
                removed_at=None, removed_reason="", updated_at=_now())
            with self._lock:
                self._backup()
                self._persist_integration(restored)
                self._state.setdefault("removed", {}).pop(restored.integration_id,
                                                          None)
            self._audit("rollback", restored, reason="RESTORED")
            with self._lock:
                self._save()
            return {"state": "OK", "integration": restored.to_dict(),
                    "note": "grants are not auto-restored; re-grant explicitly"}
        prior = rec.rollback_to
        if prior not in STATUSES or prior == STATUS_REMOVED:
            raise WebsiteIntegrationError("no rollback state recorded")
        restored = rec.with_fields(status=prior, rollback_to=None,
                                   updated_at=_now())
        with self._lock:
            self._backup()
            self._persist_integration(restored)
        self._audit("rollback", restored, reason=f"RESTORED_{prior}")
        with self._lock:
            self._save()
        return {"state": "OK", "integration": restored.to_dict()}

    # ------------------------------------------------------------ read-only checks
    def check(self, identifier: str, *, requested_by: str,
              perms: Optional[Any] = None) -> Dict[str, Any]:
        """Read-only re-verification against the current permission store."""
        rec = self.lookup(identifier)
        if rec is None:
            raise WebsiteIntegrationError(f"unknown integration {identifier!r}")
        mode = "restricted"
        grants: List[Dict[str, Any]] = []
        manifest: Optional[Dict[str, Any]] = None
        effective: Dict[str, Any] = {}
        if perms is not None:
            mode = perms.mode()[0]
            for g in perms.grants():
                if g.id in set(rec.grant_ids):
                    grants.append({"id": g.id, "capability": g.capability,
                                   "scopes": sorted(g.scopes),
                                   "active": g.is_active(),
                                   "expires_at": g.expires_at})
            if rec.manifest_link:
                try:
                    sk = validate_manifest_link(rec.manifest_link, perms)
                    manifest = {
                        "skill_id": sk.skill_id,
                        "version": sk.manifest.version,
                        "status": sk.status,
                        "active": True,
                        "source_url": sk.manifest.source,
                        "revision": sk.manifest.revision,
                        "license": sk.manifest.license,
                        "content_hash": sk.manifest.content_hash,
                        "compatibility": sk.manifest.compatibility,
                        "artifact_sha256": rec.artifact_sha256,
                        "artifact": self._artifact_status(rec),
                        "capabilities": list(sk.manifest.capabilities),
                        "scopes": list(sk.manifest.scopes),
                        "linked": True,
                    }
                except WebsiteIntegrationError as exc:
                    manifest = {"skill_id": rec.manifest_link, "active": False,
                                "linked": True, "error": str(exc)[:200]}
            try:
                from .permissions import PermissionPolicy
                policy = PermissionPolicy(perms, registry=websites_registry(self))
                for cap in rec.requested_capabilities:
                    effective[cap] = policy.effective(
                        cap, tuple(rec.requested_scopes))
            except Exception:
                effective = {}
        active_grants = [g for g in grants if g["active"]]
        expired = [g for g in grants if not g["active"]]
        status = rec.status
        linked_ok = bool(manifest and manifest.get("active"))
        linked = bool(rec.manifest_link)
        if rec.status in (STATUS_DENIED, STATUS_FAILED):
            state = "DENIED"
        elif expired:
            state = "DEGRADED"
        elif rec.status == STATUS_AUTH_REQUIRED:
            state = "AUTH_REQUIRED"
        elif rec.status == STATUS_READY and linked and not linked_ok:
            state = "DEGRADED"                     # broken/revoked link
        elif rec.status == STATUS_READY and (not linked or not active_grants):
            state = "PARTIAL"                     # valid, but no grants yet
        else:
            state = "OK"
        self._audit("check", rec, reason=rec.health.get("reason_code", ""),
                    counts={"active_grants": len(active_grants),
                            "expired": len(expired)})
        return {"state": state, "status": status, "enabled": rec.enabled,
                "mode": mode, "grants": grants, "manifest": manifest,
                "effective": effective,
                "health": redact_obj(rec.health),
                "origin": rec.origin}

    def close(self) -> None:
        pass


def website_integrations_store(cfg) -> WebsiteIntegrationStore:
    """Open (or create) the integration store at the configured/state path."""
    path = cfg.get("websites", "state_path", default="") or \
        (cfg.state_dir() / "websites.json")
    return WebsiteIntegrationStore(Path(path).expanduser())


# ---------------------------------------------------------------------------
# Permission-store linkage
# ---------------------------------------------------------------------------
def manifest_compatible(compatibility: str) -> bool:
    """True when a skill's ``compatibility`` string permits this platform.

    Empty/generic/linux values pass; an explicit other-OS-only value fails.
    """
    low = (compatibility or "").strip().lower()
    if not low or low in _COMPAT_ALL:
        return True
    if any(tok in low for tok in _COMPAT_LINUX_TOKENS):
        return True
    return not any(tok in low for tok in _COMPAT_OTHER_OS)


def validate_manifest_link(skill_id: str, perms) -> Any:
    """Return the linked active SkillRecord, or raise a typed fail-closed error.

    The linked 7E skill must be present, re-validated, ACTIVE, licensed with a
    recognized SPDX identifier/expression, hash-verified against the canonical
    artifact bytes, and compatible. A stored manifest that fails the 7E
    validator (bad license/hash/fields) is reported as SKILL_MALFORMED because
    the permission store itself refuses to materialize it; ``compatibility`` is
    the one field the 7E constructor does not validate, so this gate enforces
    it (SKILL_INCOMPATIBLE). License authenticity is validated here
    (LICENSE_INVALID / LICENSE_UNKNOWN) and the content hash is recomputed
    (SKILL_HASH_MISMATCH). Nothing here grants anything; it is only a gate that
    website ``enable``/``grant`` must pass.
    """
    from .permissions import SkillManifest, SkillValidationError
    if not skill_id or not str(skill_id).strip():
        raise WebsiteIntegrationError("SKILL_MISSING: no linked skill")
    try:
        rec = perms.skill(str(skill_id))
    except SkillValidationError as exc:
        raise WebsiteIntegrationError(
            f"SKILL_MALFORMED: skill {skill_id} stored manifest invalid: {exc}") from None
    if rec is None:
        raise WebsiteIntegrationError(
            f"SKILL_MISSING: skill {skill_id} is not installed")
    if rec.status != "active":
        raise WebsiteIntegrationError(
            f"SKILL_NOT_ACTIVE: skill {skill_id} is {rec.status} "
            "(must be active)")
    try:
        manifest = SkillManifest.from_dict(rec.manifest.to_dict())
    except SkillValidationError as exc:
        raise WebsiteIntegrationError(
            f"SKILL_MALFORMED: skill {skill_id} manifest invalid: {exc}") from None
    try:
        validate_spdx_license(manifest.license)
    except WebsiteIntegrationError:
        raise
    payload = manifest.to_dict()
    payload.pop("content_hash", None)   # hash covers the payload, not itself
    if not verify_manifest_hash(payload, manifest.content_hash):
        raise WebsiteIntegrationError(
            f"SKILL_HASH_MISMATCH: skill {skill_id} content_hash does not "
            "match the canonical artifact bytes")
    if not manifest_compatible(manifest.compatibility):
        raise WebsiteIntegrationError(
            f"SKILL_INCOMPATIBLE: skill {skill_id} is not compatible "
            "with this platform")
    return rec


def validate_website_binding(rec: "WebsiteIntegration", skill: Any) -> None:
    """Fail closed unless the linked manifest authorizes the website proposal.

    Every proposal capability must be declared by the linked manifest; every
    proposal scope must be authorized by it (exact ``web:<origin>`` or an
    explicit ``web`` scope); and the manifest tier/sensitivity/approval policy
    must be compatible with read-only website observation. A manifest that only
    declares app-control/terminal/editor/mail/destructive capabilities cannot
    authorize website observation (capability mismatch). Page/model/tool text
    never reaches these inputs.
    """
    if not rec.requested_capabilities or not rec.requested_scopes:
        raise WebsiteIntegrationError(
            "BINDING_NO_PROPOSAL: run inspect to produce a READY proposal "
            "before enabling or granting website capabilities")
    man = skill.manifest
    if man.tier != Tier.READ_ONLY:
        raise WebsiteIntegrationError(
            f"BINDING_TIER_MISMATCH: manifest tier {man.tier.value} does not "
            "authorize read-only website observation")
    if man.sensitivity == "secret":
        raise WebsiteIntegrationError(
            "BINDING_SENSITIVITY_MISMATCH: secret-sensitivity manifests "
            "cannot authorize website observation")
    if man.approval_policy == "deny":
        raise WebsiteIntegrationError(
            "BINDING_APPROVAL_MISMATCH: deny-approval manifests cannot "
            "authorize website observation")
    for cap in rec.requested_capabilities:
        if cap not in man.capabilities:
            raise WebsiteIntegrationError(
                f"BINDING_CAPABILITY_MISMATCH: proposal capability {cap!r} "
                "is not declared by the linked manifest")
    for scope in rec.requested_scopes:
        if not _scope_authorized(man.scopes, scope):
            raise WebsiteIntegrationError(
                f"BINDING_SCOPE_MISMATCH: proposal scope {scope!r} is not "
                f"authorized by the linked manifest scopes {sorted(man.scopes)}")


def websites_registry(store: WebsiteIntegrationStore):
    """Capability registry for 8E policy checks.

    Combines the host broker's trusted capabilities with the READ_ONLY
    ``web.observe.*`` proposal capabilities, scoped to every origin that has a
    validated integration record. Requests for an unrecorded origin fail
    closed as OUT_OF_SCOPE.
    """
    from .broker import build_host_broker
    reg = build_host_broker().registry
    origins = tuple(sorted({i.origin for i in store.integrations()}))
    scopes = ("web",) + tuple(f"web:{o}" for o in origins)
    for name, summary in OBSERVATION_CAPABILITIES.items():
        if reg.get(name) is None:
            reg.register(CapabilitySpec(
                name=name, version="1.0.0", tier=Tier.READ_ONLY,
                summary=summary, scopes=scopes, timeout_s=30.0))
    return reg


def effective_permissions(perms, store: WebsiteIntegrationStore,
                          rec: WebsiteIntegration) -> Dict[str, Any]:
    """Read-only effective view of the integration's grants and decisions."""
    from .permissions import PermissionPolicy
    policy = PermissionPolicy(perms, registry=websites_registry(store))
    grants = [g.to_dict() for g in perms.grants() if g.id in set(rec.grant_ids)]
    for g in grants:
        g["active"] = _grant_active(g.get("expires_at"))
    effective = {cap: policy.effective(cap, tuple(rec.requested_scopes))
                 for cap in rec.requested_capabilities}
    return {"mode": perms.mode()[0], "grants": grants, "effective": effective}


def _grant_active(expires_at: Optional[float]) -> bool:
    if expires_at is None:
        return True
    return _now() < float(expires_at)