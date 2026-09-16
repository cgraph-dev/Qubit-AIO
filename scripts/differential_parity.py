#!/usr/bin/env python3
"""Differential parity runner for the Qubit / Hermes / OpenClaw 1:1 wave.

Implements the runner required by
``docs/QUBIT_LATEST_UPSTREAM_1TO1_EXECUTION_PLAN_2026-09-16.md`` section 8 and
the frozen ``/tmp/qubit-differential/INTERFACE.md`` contract (v1).

Fail-closed rules:

* stdlib only, Python 3.12, deterministic and rerunnable;
* hermetic by default: this module never opens a network socket. Network or
  subprocess work can only happen through an adapter explicitly provided to
  :func:`run_inventory`;
* a row is never ``PASS`` unless a result was executed and compared; an absent
  binary or fixture becomes ``OPERATOR_ONLY`` with an ``operator_prerequisite``;
* only the seven allowlisted normalizers may collapse nondeterminism; any other
  key raises, and a normalizer that changes an out-of-shape value is reported as
  ``unsafe_normalizer``;
* secrets never appear in argv, subprocess env values, logs, fixtures or
  reports. Fixture env values are references (``$NAME`` / ``env:NAME``).

Exit codes: 0 clean, 1 failure/gate, 2 usage, 3 unclassifiable prerequisite,
4 internal error.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "ALLOWED_NORMALIZERS",
    "CANONICAL_QUBIT_COMMAND",
    "Diagnostic",
    "EVIDENCE_FIELDS",
    "NormalizerError",
    "SUCCESS_DISPOSITIONS",
    "TransportUnavailable",
    "apply_fail_closed_gates",
    "asymmetric_normalizers",
    "canonical_json",
    "check_help_text",
    "compare_expected_actual",
    "compare_response_headers",
    "is_openclaw_delta_row",
    "load_inventory",
    "missing_evidence_fields",
    "missing_evidence_keys",
    "normalize",
    "normalize_and_audit",
    "result_hash",
    "run_inventory",
    "selftest",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "1"
SCHEMA_VERSION = 1
GENERATED_BY = "scripts/differential_parity.py"
DEFAULT_FIXTURES_DIR = "tests/fixtures/differential"
DEFAULT_INVENTORY_DIR = Path("/tmp/qubit-differential/inventory")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_UNCLASSIFIABLE = 3
EXIT_INTERNAL = 4

FAMILIES = frozenset(
    {
        "cli", "api", "rpc", "ws", "sse", "provider", "session", "memory",
        "file", "project", "skill", "plugin", "mcp", "cron", "gateway",
        "messaging", "browser", "desktop", "board", "task", "media", "acp",
        "webhook", "auth", "approval", "recovery", "dashboard", "voice",
    }
)
DISPOSITIONS = ("PASS", "FAIL", "SAFE_EQUIVALENT", "OPERATOR_ONLY", "OPEN")
VISUAL_FAMILIES = frozenset({"dashboard", "browser", "desktop"})
REFERENCE_PROJECTS = frozenset({"hermes", "openclaw"})
# Contractual pinned upstream revisions. A reference only counts as pinned when
# its resolved commit equals the pin; the floating installed binaries do not.
CONTRACTUAL_PINS = {
    "hermes": "2237be355906fbe6065ce1815711eee52b2d646e",
    "openclaw": "3a9d69db306cd7f081e06254cb89c4bcc14a7107",
}
# Visual evidence refs required for success rows in VISUAL_FAMILIES.
VISUAL_REF_FIELDS = ("screenshot_ref", "dom_snapshot_ref", "a11y_snapshot_ref")

GATE_NAMES = (
    "unexecuted_row",
    "missing_production_caller",
    "skipped_fixture",
    "mock_replaced_real_path",
    "refusal_counted_as_success",
    "unsafe_normalizer",
    "visual_only_control",
    "voice_backend_mismatch",
    "persistence_or_recovery_diff",
    "secret_in_artifact",
    "openclaw_undisposed_addition",
    "unexecuted_open_row",
    "pass_without_reference",
    "unpinned_reference_pass",
    "inventory_coverage",
)

# Top-level adapter envelope fields that are legitimate transport plumbing and
# must never be treated as "extra" differences on their own.
ENVELOPE_FIELDS = frozenset(
    {"exit_code", "stdout", "stderr", "http_status", "json", "events",
     "headers", "duration", "persisted_state", "receipts", "meta"}
)

# A disposition that asserts parity (and therefore must be fully evidenced).
SUCCESS_DISPOSITIONS = ("PASS", "SAFE_EQUIVALENT")
# Terminal dispositions: an OpenClaw delta row is "disposed" once it is any of
# these (OPEN means the addition has not been dispositioned at all).
TERMINAL_DISPOSITIONS = ("PASS", "FAIL", "SAFE_EQUIVALENT", "OPERATOR_ONLY")

# INTERFACE v1.1 §"Plan §8/§15 coverage additions": every row/evidence result
# must carry these keys; they must be populated for PASS/SAFE_EQUIVALENT rows.
EVIDENCE_FIELDS = (
    "voice_utterance",
    "dashboard_route",
    "dashboard_control",
    "request_schema",
    "broker_policy_decision",
    "runtime_provider",
    "store_state_assertion",
    "output_event_assertion",
    "error_codes",
    "retry_recovery_proof",
    "audit_receipt_proof",
    "security_resource_proof",
    "package_proof",
    "response_headers",
    "screenshot_ref",
    "dom_snapshot_ref",
    "a11y_snapshot_ref",
    "raw_hash",
)
# The canonical Qubit CLI command (`voicebridge`) that CLI help must advertise.
CANONICAL_QUBIT_COMMAND = "voicebridge"

# OpenClaw latest-release/default-branch delta detection. A row is a delta row
# when it tracks OpenClaw but not a pinned 40-hex commit (e.g. a release tag or
# the default branch), or when it explicitly marks a delta source.
_OPENCLAW_DELTA_CHANNELS = frozenset(
    {"latest", "latest-release", "release", "default", "default-branch",
     "main", "master", "head", "trunk"}
)
_PINNED_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def canonical_json(obj: Any) -> str:
    """Canonical JSON per the frozen contract (deterministic across machines)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def result_hash(payload: Any) -> str:
    """``"sha256:" + sha256(canonical_json(payload))`` exactly as specified."""
    return "sha256:" + sha256_hex(canonical_json(payload).encode("utf-8"))


def file_hash(path: str | Path) -> str:
    """Raw-bytes hash, matching the fixture MANIFEST ``sha256`` convention."""
    return "sha256:" + sha256_hex(Path(path).read_bytes())


# --------------------------------------------------------------------------- #
# Secret handling
# --------------------------------------------------------------------------- #
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token)"
        r"\b\s*[:=]\s*\S{8,}"
    ),
)

_REF_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_ENV_REF_RE = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")


def contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(p.search(value) for p in SECRET_PATTERNS)
    if isinstance(value, Mapping):
        return any(contains_secret(k) or contains_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(v) for v in value)
    return False


def redact_text(text: str) -> str:
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub("<redacted>", out)
    return out


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _short(value: Any, limit: int = 200) -> str:
    try:
        text = canonical_json(redact(value))
    except (TypeError, ValueError):
        text = repr(redact(value))
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return redact_text(text)


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _child_path(path: str, key: Any) -> str:
    if isinstance(key, str) and _IDENT_RE.match(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(str(key), ensure_ascii=True)}]"


def scan_secrets(obj: Any, path: str = "$") -> list[str]:
    """Return the JSON paths where secret-shaped values appear (never values)."""
    found: list[str] = []
    if isinstance(obj, Mapping):
        for key in sorted(obj, key=str):
            if contains_secret(str(key)):
                found.append(_child_path(path, key))
            found.extend(scan_secrets(obj[key], _child_path(path, key)))
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            found.extend(scan_secrets(item, f"{path}[{i}]"))
    elif isinstance(obj, str) and contains_secret(obj):
        found.append(path)
    return found


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Diagnostic:
    path: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": redact_text(self.detail)}


def _diag(path: str, kind: str, detail: str) -> Diagnostic:
    return Diagnostic(path=path, kind=kind, detail=redact_text(detail))


# --------------------------------------------------------------------------- #
# Normalizers (exact allowlist)
# --------------------------------------------------------------------------- #
class NormalizerError(ValueError):
    """Raised for any normalizer key outside the frozen allowlist."""


_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?)?$"
)
_RFC2822_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\s+"
    r"\d{2}:\d{2}(:\d{2})?\s+(GMT|UTC|[+-]\d{4})$"
)
_TIME_KEYS = frozenset(
    {"timestamp", "time", "ts", "date", "datetime", "created_at", "updated_at",
     "started_at", "finished_at", "expires_at", "expiry", "epoch", "mtime"}
)
# Time-ish key suffixes recognised both at the top level and inside stringified
# JSON (e.g. ``generated_at``, ``wall_time``, ``time_ns``).
_TIME_KEY_SUFFIXES = ("_at", "_time", "_ns", "_ts")
# ISO-8601 datetimes embedded inside a larger string (e.g. a JSON blob).
_EMBEDDED_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|z|[+-]\d{2}:?\d{2})?"
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TEMP_PREFIXES = (
    "/tmp/", "/var/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/",
)
_PID_KEYS = frozenset({"pid", "ppid", "process_id", "proc_id", "child_pid", "parent_pid"})
_TOKEN_KEYS = frozenset(
    {"session_token", "token", "access_token", "refresh_token", "auth_token",
     "bearer", "bearer_token", "session_secret"}
)
_TOKEN_VAL_RE = re.compile(r"^(sess|tok|sess_|tok_)[A-Za-z0-9._\-]{8,}$")
_HASH_VAL_RE = re.compile(r"^(sha256:)?[0-9a-f]{32,64}$")
_HASH_KEYS = ("hash", "sha", "digest", "checksum", "etag", "md5", "signature")
_BRAND_RE = re.compile(r"\b(hermes|openclaw|qubit)\b", re.IGNORECASE)


def _is_time_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return low in _TIME_KEYS or low.endswith(_TIME_KEY_SUFFIXES)


def _is_epoch_candidate(key: Any, value: Any) -> bool:
    if not _is_time_key(key):
        return False
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    magnitude = abs(value)
    if isinstance(key, str) and key.lower().endswith("_ns"):
        # nanosecond epochs are ~1e18
        return 1_000_000_000_000_000 <= magnitude <= 10_000_000_000_000_000_000
    return 1_000_000_000 <= magnitude <= 10_000_000_000_000


def _replace_embedded_iso(text: str) -> str:
    """Replace ISO-8601 datetimes embedded inside a larger string."""
    return _EMBEDDED_ISO_RE.sub("<timestamp>", text)


def _normalize_time_tree(node: Any, key: Any = None) -> Any:
    """Recursively collapse time values inside a parsed JSON structure."""
    if isinstance(node, Mapping):
        return {child_key: _normalize_time_tree(value, child_key)
                for child_key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [_normalize_time_tree(value, None) for value in node]
    if key is not None and _is_time_key(key) and node is not None:
        if isinstance(node, bool):
            return node
        if isinstance(node, (int, float)):
            return "<timestamp>" if _is_epoch_candidate(key, node) else node
        if isinstance(node, str):
            if _ISO_RE.match(node) or _RFC2822_RE.match(node):
                return "<timestamp>"
            return _replace_embedded_iso(node)
        return node
    if isinstance(node, str):
        return _replace_embedded_iso(node)
    return node


def _timestamp_string_transform(text: str, key: Any = None) -> tuple[bool, str]:
    """Normalize a string that may itself contain stringified JSON timestamps."""
    parsed = _parse_json_maybe(text)
    if isinstance(parsed, (dict, list)):
        normalized = _normalize_time_tree(parsed, key)
        if normalized != parsed:
            return True, json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        return False, text
    replaced = _replace_embedded_iso(text)
    return replaced != text, replaced


def _shape_timestamp(key: Any, value: Any) -> bool:
    if isinstance(value, str):
        if _ISO_RE.match(value) or _RFC2822_RE.match(value):
            return True
        changed, _ = _timestamp_string_transform(value, key)
        return changed
    return _is_epoch_candidate(key, value)


def _norm_timestamp(value: Any, key: Any = None) -> Any:
    """Collapse timestamps, including ones embedded inside stringified JSON.

    A whole ISO/RFC value or a time-keyed epoch becomes ``<timestamp>``; a
    string that parses as JSON has its time-keyed and embedded ISO values
    collapsed, so identical logical results hash identically across runs.
    """
    if isinstance(value, str):
        if _ISO_RE.match(value) or _RFC2822_RE.match(value):
            return "<timestamp>"
        changed, replaced = _timestamp_string_transform(value, key)
        return replaced if changed else value
    if _is_epoch_candidate(key, value):
        return "<timestamp>"
    return value


def _shape_uuid(key: Any, value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _norm_uuid(value: Any, key: Any = None) -> Any:
    """Collapse canonical UUID strings only."""
    return "<uuid>" if _shape_uuid(key, value) else value


def _temp_roots() -> tuple[str, ...]:
    roots = list(_TEMP_PREFIXES)
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir.rstrip("/") + "/")
    return tuple(roots)


def _shape_temp_path(key: Any, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(value.startswith(prefix) for prefix in _temp_roots())


def _norm_temp_path(value: Any, key: Any = None) -> Any:
    """Collapse absolute paths rooted under a known temp directory."""
    return "<temp_path>" if _shape_temp_path(key, value) else value


def _shape_pid(key: Any, value: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    if low not in _PID_KEYS and not low.endswith("_pid"):
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and value.isdigit()


def _norm_pid(value: Any, key: Any = None) -> Any:
    """Collapse process ids, but only under explicitly pid-shaped keys."""
    return "<pid>" if _shape_pid(key, value) else value


def _shape_session_token(key: Any, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if isinstance(key, str) and key.lower() in _TOKEN_KEYS:
        return len(value) >= 8
    return bool(_TOKEN_VAL_RE.match(value))


def _norm_session_token(value: Any, key: Any = None) -> Any:
    """Collapse opaque session tokens by documented key or token shape."""
    return "<session_token>" if _shape_session_token(key, value) else value


def _shape_generated_hash(key: Any, value: Any) -> bool:
    if not isinstance(value, str) or not _HASH_VAL_RE.match(value):
        return False
    if value.startswith("sha256:"):
        return True
    low = key.lower() if isinstance(key, str) else ""
    return any(token in low for token in _HASH_KEYS)


def _norm_generated_hash(value: Any, key: Any = None) -> Any:
    """Collapse hex digests only under hash-ish keys or ``sha256:`` values."""
    return "<generated_hash>" if _shape_generated_hash(key, value) else value


def _shape_product_branding(key: Any, value: Any) -> bool:
    return isinstance(value, str) and bool(_BRAND_RE.search(value))


def _norm_product_branding(value: Any, key: Any = None) -> Any:
    """Collapse upstream/product brand words to a single placeholder."""
    if not _shape_product_branding(key, value):
        return value
    return _BRAND_RE.sub("<product>", value)


ALLOWED_NORMALIZERS: dict[str, Callable[..., Any]] = {
    "timestamp": _norm_timestamp,
    "uuid": _norm_uuid,
    "temp_path": _norm_temp_path,
    "pid": _norm_pid,
    "session_token": _norm_session_token,
    "generated_hash": _norm_generated_hash,
    "product_branding": _norm_product_branding,
}

_NORMALIZER_SHAPES: dict[str, Callable[[Any, Any], bool]] = {
    "timestamp": _shape_timestamp,
    "uuid": _shape_uuid,
    "temp_path": _shape_temp_path,
    "pid": _shape_pid,
    "session_token": _shape_session_token,
    "generated_hash": _shape_generated_hash,
    "product_branding": _shape_product_branding,
}

def normalize_and_audit(
    obj: Any,
    normalizers: Sequence[str],
    registry: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Normalize ``obj`` and return ``(normalized, audit_events)``.

    Every normalizer key is validated against the allowlist first. Each audit
    event records where a transform actually changed a value so that
    :func:`detect_unsafe_normalizers` can flag out-of-shape collapses.
    """
    reg = ALLOWED_NORMALIZERS if registry is None else registry
    for name in normalizers:
        if name not in ALLOWED_NORMALIZERS:
            raise NormalizerError(f"unknown normalizer: {name!r}")
        if name not in reg:
            raise NormalizerError(f"normalizer not registered: {name!r}")
    audit: list[dict[str, Any]] = []

    def walk(value: Any, path: str, key: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: walk(v, _child_path(path, k), k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v, f"{path}[{i}]", key) for i, v in enumerate(value)]
        out = value
        for name in normalizers:
            fn = reg[name]
            try:
                new = fn(out, key)
            except TypeError:
                new = fn(out)
            if new != out:
                audit.append(
                    {"normalizer": name, "path": path, "key": key,
                     "before": out, "after": new}
                )
                out = new
        return out

    return walk(obj, "$", None), audit


def normalize(obj: Any, normalizers: Sequence[str]) -> Any:
    """Public normalizer entry point; returns only the normalized object."""
    normalized, _ = normalize_and_audit(obj, normalizers)
    return normalized


def detect_unsafe_normalizers(audit: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return audit events where a normalizer collapsed an out-of-shape value."""
    unsafe: list[dict[str, Any]] = []
    for event in audit:
        shape = _NORMALIZER_SHAPES.get(event["normalizer"])
        if shape is None:
            unsafe.append(dict(event))
            continue
        if not shape(event["key"], event["before"]):
            unsafe.append(dict(event))
    return unsafe


# --------------------------------------------------------------------------- #
# Comparators
# --------------------------------------------------------------------------- #
_NONDETERMINISTIC_HEADERS = frozenset(
    {"date", "server", "x-request-id", "x-trace-id", "x-amzn-trace-id",
     "etag", "last-modified", "cf-ray", "x-runtime", "x-correlation-id",
     "traceparent", "tracestate", "x-amzn-requestid", "x-request-start",
     "x-response-time", "x-served-by", "x-cache", "age",
     "nonce", "x-nonce", "x-request-nonce",
     # WebSocket handshake fields derived from the client's random key.
     "sec-websocket-accept", "sec-websocket-key", "sec-websocket-extensions"}
)
# Placeholder substituted for a volatile transport field before hashing.
_NONDETERMINISTIC_PLACEHOLDER = "<nondeterministic>"


def _canonical_transport_value(value: Any) -> Any:
    """Collapse a volatile transport field to a stable placeholder."""
    if isinstance(value, (Mapping, list, tuple)):
        return value
    return _NONDETERMINISTIC_PLACEHOLDER


def canonical_transport_headers(headers: Any) -> dict[str, Any]:
    """Copy headers, replacing volatile values with a stable placeholder.

    Applied by the transport adapters themselves so both the raw and the
    normalized result hashes are deterministic; comparison already ignores the
    same header names via :data:`_NONDETERMINISTIC_HEADERS`.
    """
    if not isinstance(headers, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in headers.items():
        name = str(key)
        if name.lower() in _NONDETERMINISTIC_HEADERS:
            out[name] = _canonical_transport_value(value)
        else:
            out[name] = value
    return out


def _same_scalar(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return expected == actual
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, str) and isinstance(actual, str):
        return expected == actual
    return type(expected) is type(actual) and expected == actual


def _parse_json_maybe(text: Any) -> Any:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None


def _compare_headers(expected: Any, actual: Any, path: str, diags: list[Diagnostic]) -> None:
    e = {(k.lower() if isinstance(k, str) else k): v for k, v in (expected or {}).items()}
    a = {(k.lower() if isinstance(k, str) else k): v for k, v in (actual or {}).items()}
    e = {k: v for k, v in e.items() if k not in _NONDETERMINISTIC_HEADERS}
    a = {k: v for k, v in a.items() if k not in _NONDETERMINISTIC_HEADERS}
    for key in sorted(set(e) | set(a), key=str):
        if key not in a:
            diags.append(_diag(_child_path(path, key), "missing_header", "header absent"))
        elif key not in e:
            diags.append(_diag(_child_path(path, key), "extra_header", "unexpected header"))
        elif not _same_scalar(e[key], a[key]):
            diags.append(
                _diag(_child_path(path, key), "header",
                      f"expected={_short(e[key])} actual={_short(a[key])}")
            )


def _compare_set(expected: Any, actual: Any, path: str, diags: list[Diagnostic]) -> None:
    e = {str(x) for x in (expected or [])}
    a = {str(x) for x in (actual or [])}
    for item in sorted(e - a):
        diags.append(_diag(_child_path(path, item), "missing_scope", f"missing {item!r}"))
    for item in sorted(a - e):
        diags.append(_diag(_child_path(path, item), "extra_scope", f"extra {item!r}"))


def _compare_node(
    expected: Any,
    actual: Any,
    path: str,
    diags: list[Diagnostic],
    template: bool,
    normalizers: Sequence[str] | None = None,
) -> None:
    """Compare an expected contract against actual, reporting exact paths.

    A mapping asserts only the keys present in ``expected`` unless it carries
    ``"closed": true``, in which case extra actual keys are differences. This
    keeps the adapter envelope (stdout/stderr/exit_code/...) out of the diff
    while still supporting strict objects. ``*_schema`` constructs and
    ``closed`` are honoured at any depth; ``stdout``/``stderr`` JSON is parsed
    and normalized before comparison.
    """
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            diags.append(_diag(path, "type", f"expected object, actual {type(actual).__name__}"))
            return
        closed = bool(expected.get("closed"))
        declared_keys = [key for key in expected if key != "closed"]
        for key in sorted(declared_keys, key=str):
            if key in ("stdout_schema", "stderr_schema", "json_schema") and key not in actual:
                continue
            if key not in actual:
                diags.append(_diag(_child_path(path, key), "missing", "key absent"))
        if closed:
            for key in sorted(actual, key=str):
                if key in expected or key == "closed":
                    continue
                if key == "json" and "json_schema" in expected:
                    continue
                if key in ("stdout", "stderr") and f"{key}_schema" in expected:
                    continue
                diags.append(_diag(_child_path(path, key), "extra", "unexpected key"))
        for key in sorted(declared_keys, key=str):
            child = _child_path(path, key)
            if key in ("stdout_schema", "stderr_schema"):
                stream = key[: -len("_schema")]
                if stream not in actual:
                    continue
                parsed = _parse_json_maybe(actual[stream])
                if parsed is None:
                    if expected[key]:
                        diags.append(
                            _diag(child, "stdout_parse", f"{stream} is not JSON as required")
                        )
                else:
                    if normalizers:
                        parsed = normalize(parsed, list(normalizers))
                    _compare_node(expected[key], parsed, child, diags, True, normalizers)
                continue
            if key == "json_schema":
                if "json" not in actual:
                    continue
                target = actual["json"]
                if normalizers:
                    target = normalize(target, list(normalizers))
                _compare_node(expected[key], target, child, diags, True, normalizers)
                continue
            if key == "headers":
                _compare_headers(expected[key], actual.get(key, {}), child, diags)
                continue
            if key == "scopes":
                _compare_set(expected[key], actual.get(key, []), child, diags)
                continue
            if key not in actual:
                continue
            _compare_node(expected[key], actual[key], child, diags, template, normalizers)
        return

    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            diags.append(_diag(path, "type", f"expected list, actual {type(actual).__name__}"))
            return
        if template:
            for i, item in enumerate(expected):
                if i < len(actual):
                    _compare_node(item, actual[i], f"{path}[{i}]", diags, True, normalizers)
                else:
                    diags.append(_diag(f"{path}[{i}]", "missing", "template item absent"))
            return
        if len(expected) != len(actual):
            diags.append(
                _diag(path, "length", f"expected {len(expected)} items, actual {len(actual)}")
            )
        for i in range(min(len(expected), len(actual))):
            _compare_node(expected[i], actual[i], f"{path}[{i}]", diags, False, normalizers)
        return

    if not _same_scalar(expected, actual):
        diags.append(
            _diag(path, "value", f"expected={_short(expected)} actual={_short(actual)}")
        )


def compare_expected_actual(
    expected: Any,
    actual: Any,
    path: str = "$",
    normalizers: Sequence[str] | None = None,
) -> list[Diagnostic]:
    """Structured SYMMETRIC diff of expected vs actual, reporting exact paths.

    When ``normalizers`` is given it is applied to BOTH sides before comparing
    (never one side only). Only keys present in ``expected`` are asserted unless
    the expected object marks itself ``"closed": true``; parsed ``stdout`` /
    ``stderr`` JSON is normalized too. ``headers`` ignore nondeterminism,
    ``scopes`` compare as sets, and nested/event/persisted-state recursion is
    unchanged.
    """
    if normalizers:
        expected = normalize(expected, list(normalizers))
        actual = normalize(actual, list(normalizers))
    diags: list[Diagnostic] = []
    _compare_node(expected, actual, path, diags, False, normalizers)
    return diags


_MISSING = object()


def _jsonpath(parts: Sequence[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and _IDENT_RE.match(part):
            path += f".{part}"
        else:
            path += f"[{json.dumps(str(part), ensure_ascii=True)}]"
    return path


def _raw_at_path(obj: Any, parts: Sequence[Any]) -> Any:
    cur = obj
    for part in parts:
        if isinstance(part, int):
            if not isinstance(cur, (list, tuple)) or part >= len(cur):
                return _MISSING
            cur = cur[part]
        else:
            if not isinstance(cur, Mapping) or part not in cur:
                return _MISSING
            cur = cur[part]
    return cur


def _normalizer_change_paths(obj: Any, normalizers: Sequence[str]) -> set[tuple]:
    """JSON-path tuples where a normalizer actually changed a value."""
    changed: set[tuple] = set()

    def walk(value: Any, path: tuple, key: Any) -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                walk(child_value, path + (child_key,), child_key)
        elif isinstance(value, (list, tuple)):
            for index, child_value in enumerate(value):
                walk(child_value, path + (index,), key)
        else:
            out = value
            for name in normalizers:
                fn = ALLOWED_NORMALIZERS[name]
                try:
                    new = fn(out, key)
                except TypeError:
                    new = fn(out)
                if new != out:
                    changed.add(path)
                    out = new

    walk(obj, (), None)
    return changed


def asymmetric_normalizers(
    expected: Any, actual: Any, normalizers: Sequence[str] | None
) -> list[str]:
    """Paths where a normalizer collapsed exactly one side's differing value.

    Normalization must be symmetric; a one-sided collapse that hides a
    difference is an ``unsafe_normalizer`` trigger.
    """
    names = [name for name in (normalizers or []) if name in ALLOWED_NORMALIZERS]
    if not names:
        return []
    expected_paths = _normalizer_change_paths(expected, names)
    actual_paths = _normalizer_change_paths(actual, names)
    flagged: list[str] = []
    for path in sorted(expected_paths ^ actual_paths, key=str):
        expected_raw = _raw_at_path(expected, path)
        actual_raw = _raw_at_path(actual, path)
        if expected_raw is _MISSING or actual_raw is _MISSING:
            continue
        if expected_raw != actual_raw:
            flagged.append(_jsonpath(path))
    return sorted(flagged)


def compare_response_headers(
    expected: Any, actual: Any, path: str = "$"
) -> list[Diagnostic]:
    """Compare HTTP response headers, ignoring nondeterministic ones.

    Only headers outside :data:`_NONDETERMINISTIC_HEADERS` are compared, so a
    changing ``Date``/``Server``/``ETag``/trace id never reports a difference,
    while a real ``Content-Type``/``Location``/wire-contract change does.
    """
    diags: list[Diagnostic] = []
    _compare_headers(expected, actual, path, diags)
    return diags


def _extract_help_text(actual: Any) -> str:
    """Pull help text out of a string or a normalized adapter payload."""
    if isinstance(actual, str):
        return actual
    if isinstance(actual, Mapping):
        for key in ("help", "help_text", "usage", "stdout"):
            value = actual.get(key)
            if isinstance(value, str):
                return value
    return ""


def check_help_text(
    actual: Any,
    required_substrings: Sequence[str] | None = None,
    path: str = "$.help",
) -> list[Diagnostic]:
    """Assert CLI help text advertises required markers (canonical command).

    ``actual`` may be the raw help string / stdout or a normalized result
    payload. Missing markers are reported as ``help_missing`` diagnostics at
    the exact help path; this is fail-closed (absence is a difference).
    """
    if required_substrings is None:
        required_substrings = (CANONICAL_QUBIT_COMMAND,)
    text = _extract_help_text(actual)
    diags: list[Diagnostic] = []
    for needle in required_substrings:
        if not needle:
            continue
        if needle not in text:
            diags.append(
                _diag(path, "help_missing",
                      f"help text does not contain canonical command {needle!r}")
            )
    return diags


def is_openclaw_delta_row(row: Mapping[str, Any]) -> bool:
    """True when a row tracks the OpenClaw latest-release/default-branch delta.

    Pinned OpenClaw rows (40-hex ``upstream_commit``) are contractual, not
    delta, rows. Anything tracking a release tag, a branch name, a mutable ref
    or an explicit delta marker is a delta row and must be dispositioned.
    """
    if str(row.get("upstream_project") or "").lower() != "openclaw":
        return False
    if row.get("openclaw_delta") is True or row.get("delta") is True:
        return True
    if row.get("undisposed") is True:
        return True
    # Explicit latest-vs-pin delta markers emitted by the upstream delta scan.
    if str(row.get("delta_role") or "").strip():
        return True
    if str(row.get("delta_vs_pin_commit") or "").strip():
        return True
    if str(row.get("delta_note") or "").strip():
        return True
    for key in ("delta_channel", "delta_source", "source_channel", "upstream_ref",
                "upstream_branch", "branch", "channel", "release_channel"):
        value = str(row.get(key) or "").strip().lower()
        if value and (value in _OPENCLAW_DELTA_CHANNELS
                      or not _PINNED_COMMIT_RE.match(value)):
            return True
    commit = str(row.get("upstream_commit") or "").strip().lower()
    if not commit:
        return True
    return not _PINNED_COMMIT_RE.match(commit)


def _has_qubit_disposition_and_proof(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> bool:
    """A delta row is disposed when it has a terminal disposition AND proof."""
    disposition = str(res.get("disposition") or row.get("disposition") or "OPEN")
    if disposition not in TERMINAL_DISPOSITIONS:
        return False
    proof = (
        res.get("production_caller")
        or row.get("qubit_symbol")
        or row.get("qubit_path")
        or (row.get("trace_refs") or [])
        or res.get("executed")
    )
    return bool(proof)


def missing_evidence_keys(result: Mapping[str, Any]) -> list[str]:
    """Evidence keys that are absent entirely (must exist on every result)."""
    return [field for field in EVIDENCE_FIELDS if field not in result]


def missing_evidence_fields(
    result: Mapping[str, Any], notes: Mapping[str, Any] | None = None
) -> list[str]:
    """Evidence keys missing/``null`` for a PASS/SAFE_EQUIVALENT result.

    Non-success dispositions (OPEN, OPERATOR_ONLY) are exempt here: their
    omissions are caught by the all-inventory coverage test, not this check.
    A field that carries an explicit non-applicability note (fixture
    ``evidence_notes``) is considered accounted for, so "not exposed by this
    surface" is a documented absence rather than a missing proof.
    """
    if str(result.get("disposition") or "OPEN") not in SUCCESS_DISPOSITIONS:
        return []
    notes = notes or {}
    family = str(result.get("family") or "")
    missing: list[str] = []
    for field in EVIDENCE_FIELDS:
        if field in result and result.get(field) is not None:
            continue
        # Visual evidence refs are only required in the visual families.
        if field in VISUAL_REF_FIELDS and family not in VISUAL_FAMILIES:
            continue
        # raw_hash is only required where the raw result is byte-comparable.
        if field == "raw_hash" and result.get("qubit_raw_result_hash") is None:
            continue
        note = notes.get(field)
        if isinstance(note, str) and note.strip():
            continue
        missing.append(field)
    return missing


def _family_diagnostics(
    family: str, expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[Diagnostic]:
    """Family-specific additional checks on top of the structured diff."""
    diags: list[Diagnostic] = []
    if family in ("ws", "rpc", "gateway"):
        for key in ("handshake", "scopes", "events", "reconnect", "auth"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    if family == "sse" and ("events" in expected or "events" in actual):
        _compare_node(expected.get("events"), actual.get("events"), "$.events", diags, False)
    if family in ("session", "memory", "board", "task", "recovery", "file", "project"):
        for key in ("persisted_state", "receipts"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    if family == "voice":
        for key in ("voice_operation", "resolved_identity", "confirmation"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    if family == "provider" and ("resolved_identity" in expected or "resolved_identity" in actual):
        _compare_node(
            expected.get("resolved_identity"), actual.get("resolved_identity"),
            "$.resolved_identity", diags, False,
        )
    if family == "cli" and "help_contains" in expected:
        required = expected.get("help_contains")
        if not isinstance(required, (list, tuple)):
            required = [required]
        diags += check_help_text(actual, [str(x) for x in required], "$.help")
    if family in ("api", "gateway", "webhook", "rpc", "ws", "sse"):
        if "response_headers" in expected or "response_headers" in actual:
            diags += compare_response_headers(
                expected.get("response_headers"), actual.get("response_headers"),
                "$.response_headers",
            )
    if family in VISUAL_FAMILIES:
        for key in ("dom", "accessibility", "backend_operation"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
        for key in ("screenshot", "screenshots", "screenshot_evidence",
                    "dom_snapshot", "accessibility_snapshot"):
            if key in expected:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    return diags


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class CallResult:
    """Normalized adapter result; absent fields are omitted from payloads."""

    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    http_status: int | None = None
    json: Any = None
    events: Any = None
    persisted_state: Any = None
    headers: Any = None
    dom: Any = None
    accessibility: Any = None
    voice_operation: Any = None
    resolved_identity: Any = None
    receipts: Any = None
    backend_operation: Any = None
    duration_ms: int | None = None
    transport: str | None = None
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field in dataclasses.fields(self):
            if field.name in ("meta", "duration_ms"):
                # ``duration_ms`` is volatile wall-clock evidence; it is
                # reported on the result but never hashed.
                continue
            value = getattr(self, field.name)
            if value is not None:
                payload[field.name] = value
        return payload


class Adapter:
    """Protocol: an adapter turns a request spec into a :class:`CallResult`."""

    name = "adapter"
    project = "qubit"
    is_mock = False

    def available(self) -> bool:
        raise NotImplementedError

    def execute(self, request: Mapping[str, Any]) -> CallResult:
        raise NotImplementedError


def _resolve_env_ref(value: Any) -> tuple[bool, str, str | None]:
    """Resolve a fixture env value. Returns (ok, resolved, missing_name)."""
    if not isinstance(value, str):
        return True, str(value), None
    ref = _REF_RE.match(value)
    if ref:
        name = ref.group(1)
        if name in os.environ:
            return True, os.environ[name], None
        return False, "", name
    ref = _ENV_REF_RE.match(value)
    if ref:
        name = ref.group(1)
        if name in os.environ:
            return True, os.environ[name], None
        return False, "", name
    return True, value, None


# --------------------------------------------------------------------------- #
# Transport adapters (v1.2 contract; stdlib only)
# --------------------------------------------------------------------------- #
class TransportUnavailable(RuntimeError):
    """A declared transport could not resolve a URL/token or could not connect.

    ``_run_row`` maps this to OPERATOR_ONLY with an operator_prerequisite: an
    unreachable transport is never PASS (and is not automatically FAIL).
    """


def _transport_timeout() -> float:
    raw = os.environ.get("QUBIT_DIFFERENTIAL_TRANSPORT_TIMEOUT")
    if raw:
        try:
            return max(0.5, float(raw))
        except ValueError:
            pass
    return 5.0


def _redact_url(url: str) -> str:
    """Strip userinfo/query/fragment so no token can reach a report."""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError:
        return "<url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _join_url(base: str, path: Any) -> str:
    if not path:
        return base
    text = str(path)
    if text.startswith(("http://", "https://", "ws://", "wss://")):
        return text
    return base.rstrip("/") + "/" + text.lstrip("/")


def _looks_like_env_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(
        _REF_RE.match(value) or _ENV_REF_RE.match(value)
    )


def _resolve_base_url(project: str, spec: Mapping[str, Any]) -> tuple[str | None, str]:
    """Resolve the base URL from a declared ref or the project env var."""
    declared = spec.get("base_url_ref") or spec.get("base_url")
    if isinstance(declared, str) and declared.strip():
        declared = declared.strip()
        if _looks_like_env_ref(declared):
            ok, resolved, missing = _resolve_env_ref(declared)
            if not ok or not resolved:
                return None, f"base URL reference {missing!r} is not set"
            return resolved.rstrip("/"), ""
        return declared.rstrip("/"), ""
    env_name = f"QUBIT_DIFFERENTIAL_{str(project).upper()}_URL"
    value = os.environ.get(env_name)
    if value and value.strip():
        return value.strip().rstrip("/"), ""
    return None, f"no base URL for {project} (set {env_name})"


def _resolve_transport_headers(
    project: str, spec: Mapping[str, Any]
) -> dict[str, str]:
    """Build outgoing headers; token VALUES resolve from env refs only.

    The resolved token is placed on the outgoing request only and is never
    copied into a CallResult, log, fixture or report.
    """
    headers: dict[str, str] = {}
    raw_headers = spec.get("headers")
    if isinstance(raw_headers, Mapping):
        for key, value in raw_headers.items():
            if _looks_like_env_ref(value):
                ok, resolved, missing = _resolve_env_ref(value)
                if not ok:
                    raise TransportUnavailable(
                        f"header reference {missing!r} is not set")
                headers[str(key)] = resolved
            else:
                headers[str(key)] = str(value)
    auth = spec.get("auth")
    if isinstance(auth, Mapping):
        header = auth.get("header")
        value_ref = auth.get("value_ref") or auth.get("value")
        if header:
            if value_ref is None:
                raise TransportUnavailable(
                    f"auth header {header!r} declared without a value reference")
            ok, resolved, missing = _resolve_env_ref(value_ref)
            if not ok:
                raise TransportUnavailable(
                    f"auth reference {missing!r} is not set")
            headers[str(header)] = resolved
    return headers


def _http_request(
    project: str, spec: Mapping[str, Any], timeout: float, transport: str
) -> CallResult:
    """Single HTTP request (also used by the rpc transport)."""
    base, reason = _resolve_base_url(project, spec)
    if base is None:
        raise TransportUnavailable(reason)
    headers = _resolve_transport_headers(project, spec)
    method = str(spec.get("method") or "GET").upper()
    body = spec.get("body")
    data: bytes | None = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
    url = _join_url(base, spec.get("path"))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            raw_headers = dict(response.headers.items()) if response.headers else {}
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw_headers = dict(exc.headers.items()) if exc.headers else {}
        raw = exc.read()
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, TimeoutError, OSError) as exc:
        raise TransportUnavailable(
            f"{project} {transport} transport unreachable at "
            f"{_redact_url(url)}: {exc}") from exc
    duration = int((time.monotonic() - start) * 1000)
    text = raw.decode("utf-8", "replace")
    return CallResult(
        http_status=status,
        headers=canonical_transport_headers(raw_headers),
        json=_parse_json_maybe(text),
        stdout=text,
        duration_ms=duration,
        transport=transport,
        meta={
            "adapter": project,
            "transport": transport,
            "url": _redact_url(url),
            "method": method,
        },
    )


_SSE_MAX_EVENTS = 500
_SSE_MAX_BYTES = 2_000_000


def _sse_request(
    project: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """Server-sent events, preserving server order and bounding the read."""
    base, reason = _resolve_base_url(project, spec)
    if base is None:
        raise TransportUnavailable(reason)
    headers = _resolve_transport_headers(project, spec)
    headers.setdefault("Accept", "text/event-stream")
    method = str(spec.get("method") or "GET").upper()
    body = spec.get("body")
    data: bytes | None = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
    url = _join_url(base, spec.get("path"))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw_headers = dict(exc.headers.items()) if exc.headers else {}
        text = exc.read().decode("utf-8", "replace")
        return CallResult(
            http_status=exc.code,
            headers=canonical_transport_headers(raw_headers),
            json=_parse_json_maybe(text),
            stdout=text,
            events=[],
            duration_ms=int((time.monotonic() - start) * 1000),
            transport="sse",
            meta={"adapter": project, "transport": "sse", "url": _redact_url(url)},
        )
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, TimeoutError, OSError) as exc:
        raise TransportUnavailable(
            f"{project} sse transport unreachable at "
            f"{_redact_url(url)}: {exc}") from exc

    status = getattr(response, "status", None)
    raw_headers = dict(response.headers.items()) if response.headers else {}
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    truncated = False
    truncation_reason: str | None = None
    total_bytes = 0
    try:
        with response:
            for raw_line in response:
                total_bytes += len(raw_line)
                if total_bytes > _SSE_MAX_BYTES or len(events) >= _SSE_MAX_EVENTS:
                    truncated = True
                    truncation_reason = "event/byte bound reached"
                    break
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if current:
                        events.append(current)
                        current = {}
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    current["data"] = (
                        current["data"] + "\n" + value if "data" in current else value
                    )
                elif field in ("event", "id", "retry"):
                    current[field] = value
    except (socket.timeout, TimeoutError, ssl.SSLError, OSError) as exc:
        truncated = True
        truncation_reason = f"stream ended: {exc}"
    if current:
        events.append(current)
    persisted: dict[str, Any] = {"truncated": truncated, "event_count": len(events)}
    if truncation_reason:
        persisted["truncation_reason"] = truncation_reason
    return CallResult(
        http_status=status,
        headers=canonical_transport_headers(raw_headers),
        events=events,
        persisted_state=persisted,
        duration_ms=int((time.monotonic() - start) * 1000),
        transport="sse",
        meta={"adapter": project, "transport": "sse", "url": _redact_url(url)},
    )


_WS_MAX_FRAMES = 50
_WS_MAX_BYTES = 1_000_000


def _recv_exact(sock: socket.socket, buf: bytes, n: int) -> tuple[bytes, bytes]:
    while len(buf) < n:
        chunk = sock.recv(max(4096, n - len(buf)))
        if not chunk:
            raise ConnectionError("connection closed by peer")
        buf += chunk
    return buf[:n], buf[n:]


def _read_ws_frame(sock: socket.socket, buf: bytes) -> tuple[bool, int, bytes, bytes]:
    """Read one RFC6455 frame. Server frames are unmasked."""
    head, buf = _recv_exact(sock, buf, 2)
    first, second = head[0], head[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        ext, buf = _recv_exact(sock, buf, 2)
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext, buf = _recv_exact(sock, buf, 8)
        length = int.from_bytes(ext, "big")
    mask = b""
    if masked:
        mask, buf = _recv_exact(sock, buf, 4)
    payload, buf = _recv_exact(sock, buf, length)
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return fin, opcode, payload, buf


def _send_ws_text(sock: socket.socket, text: str) -> None:
    """Send a client text frame (clients MUST mask)."""
    data = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytes([0x81])
    length = len(data)
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 65536:
        header += bytes([0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header += bytes([0x80 | 127]) + length.to_bytes(8, "big")
    masked = bytes(data[i] ^ mask[i % 4] for i in range(length))
    sock.sendall(header + mask + masked)


def _collect_ws_frames(
    sock: socket.socket, initial: bytes, spec: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], bool, str | None]:
    events: list[dict[str, Any]] = []
    buf = initial
    truncated = False
    reason: str | None = None
    total_bytes = 0
    while True:
        if len(events) >= _WS_MAX_FRAMES or total_bytes > _WS_MAX_BYTES:
            truncated = True
            reason = "frame/byte bound reached"
            break
        try:
            fin, opcode, payload, buf = _read_ws_frame(sock, buf)
        except (socket.timeout, TimeoutError, ssl.SSLError, OSError, ConnectionError) as exc:
            truncated = True
            reason = f"read ended: {exc}"
            break
        total_bytes += len(payload)
        if opcode == 0x1:  # text
            events.append({"event": "message", "data": payload.decode("utf-8", "replace")})
        elif opcode == 0x2:  # binary
            events.append({"event": "binary", "length": len(payload)})
        elif opcode == 0x8:  # close
            code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
            events.append({"event": "close", "code": code,
                           "reason": payload[2:].decode("utf-8", "replace")})
            break
        elif opcode == 0x9:  # ping -> pong
            events.append({"event": "ping"})
            try:
                mask = os.urandom(4)
                masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
                header = bytes([0x8A, 0x80 | len(payload)]) if len(payload) < 126 else None
                if header is not None:
                    sock.sendall(header + mask + masked)
            except OSError:
                pass
        elif opcode == 0xA:  # pong
            events.append({"event": "pong"})
        if fin is False:
            # Fragment continuation is out of scope; note it rather than guess.
            events.append({"event": "fragment-unsupported"})
    return events, truncated, reason


def _ws_request(
    project: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """Minimal RFC6455 client: handshake + bounded frame capture."""
    base, reason = _resolve_base_url(project, spec)
    if base is None:
        raise TransportUnavailable(reason)
    parts = urllib.parse.urlsplit(base)
    secure = parts.scheme in ("https", "wss")
    host = parts.hostname
    if not host:
        raise TransportUnavailable(f"ws base URL has no host: {_redact_url(base)}")
    port = parts.port or (443 if secure else 80)
    full = _join_url(base, spec.get("path"))
    full_parts = urllib.parse.urlsplit(full)
    resource = full_parts.path or "/"
    if full_parts.query:
        resource += "?" + full_parts.query
    headers = _resolve_transport_headers(project, spec)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {resource} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    request_bytes = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")
    start = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        sock.sendall(request_bytes)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
    except (socket.timeout, TimeoutError, ssl.SSLError, OSError) as exc:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        raise TransportUnavailable(
            f"{project} ws transport unreachable at {_redact_url(full)}: {exc}"
        ) from exc

    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace") if head else ""
    match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d+)", status_line)
    status = int(match.group(1)) if match else None
    raw_headers: dict[str, Any] = {}
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.decode("latin-1", "replace").partition(":")
        if name.strip():
            raw_headers[name.strip()] = value.strip()

    events: list[dict[str, Any]] = []
    truncated = False
    truncation_reason: str | None = None
    if status == 101:
        for message in spec.get("messages") or []:
            text = message if isinstance(message, str) else json.dumps(
                message, sort_keys=True, ensure_ascii=True)
            try:
                _send_ws_text(sock, text)
            except OSError as exc:
                truncated = True
                truncation_reason = f"send failed: {exc}"
                break
        else:
            events, truncated, truncation_reason = _collect_ws_frames(sock, rest, spec)
    else:
        truncated = True
        truncation_reason = f"handshake returned {status}"
    try:
        sock.close()
    except OSError:
        pass

    persisted: dict[str, Any] = {
        "handshake_status": status,
        "reconnect_attempts": 0,
        "truncated": truncated,
        "event_count": len(events),
    }
    if truncation_reason:
        persisted["truncation_reason"] = truncation_reason
    return CallResult(
        http_status=status,
        headers=canonical_transport_headers(raw_headers),
        events=events,
        persisted_state=persisted,
        duration_ms=int((time.monotonic() - start) * 1000),
        transport="ws",
        meta={"adapter": project, "transport": "ws", "url": _redact_url(full)},
    )


_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)


def _rpc_request(
    project: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """JSON-RPC-over-HTTP: build/forward the request envelope."""
    rpc_method = spec.get("rpc_method")
    params = spec.get("params")
    body = spec.get("body")
    declared_method = spec.get("method")
    if (rpc_method is None and isinstance(declared_method, str)
            and declared_method.upper() not in _HTTP_METHODS):
        rpc_method = declared_method
    if body is None and (rpc_method is not None or params is not None):
        body = {"type": "req", "id": 1, "method": rpc_method,
                "params": params if params is not None else {}}
    http_method = spec.get("http_method") or ("POST" if body is not None else "GET")
    http_spec = dict(spec)
    http_spec["method"] = http_method
    http_spec["body"] = body
    return _http_request(project, http_spec, timeout, "rpc")


def _execute_transport(
    project: str, kind: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """Dispatch a transport spec, supporting an optional multi-step sequence."""
    sequence = spec.get("sequence")
    if isinstance(sequence, list) and sequence:
        return _execute_transport_sequence(project, kind, spec, sequence, timeout)
    if kind == "http":
        return _http_request(project, spec, timeout, "http")
    if kind == "rpc":
        return _rpc_request(project, spec, timeout)
    if kind == "sse":
        return _sse_request(project, spec, timeout)
    if kind == "ws":
        return _ws_request(project, spec, timeout)
    raise NotImplementedError(f"{project}: kind {kind!r} is not a transport")


def _sequence_step_executes(step: Mapping[str, Any]) -> bool:
    """True when a sequence step describes a real network operation.

    Purely declarative steps (``handshake`` expectations, ``reconnect``
    attempt counts) are recorded, not executed as separate connections.
    """
    name = str(step.get("step") or "")
    if name in ("handshake", "reconnect", "expect"):
        return False
    if "attempts" in step and name == "reconnect":
        return False
    return any(key in step for key in ("path", "body", "argv", "params"))

def _execute_transport_sequence(
    project: str,
    kind: str,
    spec: Mapping[str, Any],
    sequence: Sequence[Any],
    timeout: float,
) -> CallResult:
    """Run a multi-step handshake/reconnect sequence, recording each step."""
    merged_events: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    persisted: dict[str, Any] = {"sequence": steps, "reconnect_attempts": 0}
    last: CallResult | None = None
    for index, raw_step in enumerate(sequence):
        step = dict(raw_step) if isinstance(raw_step, Mapping) else {}
        name = str(step.get("step") or "")
        if "attempts" in step:
            try:
                persisted["reconnect_attempts"] = int(step.get("attempts") or 0)
            except (TypeError, ValueError):
                pass
        if not _sequence_step_executes(step):
            steps.append({"step": index, "name": name, "executed": False,
                          "declarative": True})
            continue
        step_kind = str(step.get("kind")
                        or step.get("transport")
                        or (kind if kind != "sequence" else "http"))
        for inherited in ("base_url", "base_url_ref", "auth", "headers", "path"):
            if inherited not in step and inherited in spec:
                step[inherited] = spec[inherited]
        try:
            call = _execute_transport(project, step_kind, step, timeout)
        except TransportUnavailable as exc:
            persisted["reconnect_attempts"] = int(persisted["reconnect_attempts"]) + 1
            steps.append({"step": index, "name": name, "kind": step_kind,
                          "executed": False, "reason": str(exc)})
            if last is None:
                raise
            persisted["truncated"] = True
            persisted["truncation_reason"] = f"step {index} unavailable: {exc}"
            break
        last = call
        steps.append({"step": index, "name": name, "kind": step_kind,
                      "executed": True, "http_status": call.http_status})
        if isinstance(call.events, list):
            merged_events.extend(call.events)
        if isinstance(call.persisted_state, Mapping):
            for key, value in call.persisted_state.items():
                persisted.setdefault(key, value)
    if last is None:
        raise TransportUnavailable(
            f"{project} sequence had no executed step (all unavailable)")
    persisted.setdefault("truncated", False)
    return CallResult(
        exit_code=last.exit_code,
        stdout=last.stdout,
        stderr=last.stderr,
        http_status=last.http_status,
        json=last.json,
        events=merged_events,
        persisted_state=persisted,
        headers=last.headers,
        duration_ms=last.duration_ms,
        transport=last.transport,
        meta=last.meta,
    )


class _SubprocessAdapter(Adapter):
    project = "qubit"

    def __init__(self, binary: str | None, env_var: str) -> None:
        self.env_var = env_var
        self.binary = os.environ.get(env_var) or binary
        self.timeout = float(os.environ.get("QUBIT_DIFFERENTIAL_TIMEOUT", "60"))

    def available(self) -> bool:
        """A transport URL makes an adapter usable even without a local binary."""
        if self.binary is not None and Path(self.binary).exists():
            return True
        url = os.environ.get(f"QUBIT_DIFFERENTIAL_{str(self.project).upper()}_URL")
        return bool(url and url.strip())

    def execute(self, request: Mapping[str, Any]) -> CallResult:
        kind = str(request.get("kind") or "cli")
        if kind == "cli":
            return self._execute_cli(request)
        if kind in ("http", "sse", "ws", "rpc"):
            return _execute_transport(
                str(self.project), kind, request, _transport_timeout())
        raise NotImplementedError(
            f"{self.name}: kind {kind!r} requires a dedicated transport adapter"
        )

    def _execute_cli(self, request: Mapping[str, Any]) -> CallResult:
        argv = [str(a) for a in request.get("argv", [])]
        if argv and argv[0] in ("voicebridge", "qubit", "hermes", "openclaw"):
            argv = argv[1:]
        env = dict(os.environ)
        for key, raw in (request.get("env") or {}).items():
            ok, resolved, missing = _resolve_env_ref(raw)
            if not ok:
                raise RuntimeError(f"environment reference {missing!r} is not set")
            env[str(key)] = resolved
        proc = subprocess.run(
            [self.binary, *argv],
            input=request.get("stdin"),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
            cwd=request.get("cwd") or str(REPO_ROOT),
            check=False,
        )
        return CallResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            meta={"adapter": self.name, "binary": self.binary, "argv": argv},
        )


class QubitAdapter(_SubprocessAdapter):
    name = "qubit"
    project = "qubit"

    def __init__(self, binary: str | None = None) -> None:
        if binary is None:
            binary = (
                shutil.which("voicebridge")
                or shutil.which("qubit")
                or str(REPO_ROOT / ".venv" / "bin" / "voicebridge")
            )
        super().__init__(binary, "QUBIT_BIN")


class ReferenceAdapter(_SubprocessAdapter):
    name = "reference"

    def __init__(self, project: str, binary: str | None = None) -> None:
        self.project = project
        self.name = project
        env_var = f"QUBIT_DIFFERENTIAL_{project.upper()}_BIN"
        if binary is None:
            binary = shutil.which(project)
        super().__init__(binary, env_var)


_VERSION_CACHE: dict[str, str | None] = {}


def _probe_version(adapter: Adapter) -> str | None:
    """Record the resolved binary's ``--version`` first line (best effort).

    The pinned clones are source trees without a built entrypoint, so the live
    binary is the runnable reference; its version is captured as provenance and
    is never part of the result hashes.
    """
    binary = getattr(adapter, "binary", None)
    if not binary:
        return None
    if binary in _VERSION_CACHE:
        return _VERSION_CACHE[binary]
    version: str | None = None
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(REPO_ROOT),
            check=False,
        )
        text = (proc.stdout or proc.stderr or "").strip()
        version = text.splitlines()[0] if text else ""
    except (OSError, subprocess.SubprocessError):
        version = None
    _VERSION_CACHE[binary] = version
    return version


def _resolve_reference_commit(
    project: str, adapter: Adapter, version: str | None
) -> str | None:
    """Resolve the executed reference's commit (env override, else --version)."""
    override = os.environ.get(f"QUBIT_DIFFERENTIAL_{project.upper()}_COMMIT")
    if override and override.strip():
        return override.strip().lower()
    if isinstance(version, str):
        candidates = re.findall(r"\b[0-9a-f]{7,40}\b", version, re.IGNORECASE)
        if candidates:
            return candidates[0].lower()
    return None


def _reference_is_pinned(project: str, commit: str | None) -> bool:
    """True only when the executed reference commit equals the contractual pin."""
    pin = CONTRACTUAL_PINS.get(project)
    if not pin or not commit:
        return False
    commit = commit.lower()
    pin = pin.lower()
    return pin.startswith(commit) or commit.startswith(pin)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def default_adapters(offline: bool | None = None) -> dict[str, Adapter]:
    """Build default adapters; reference adapters run only when not offline."""
    if offline is None:
        offline = _env_truthy("QUBIT_DIFFERENTIAL_OFFLINE")
    adapters: dict[str, Adapter] = {"qubit": QubitAdapter()}
    if not offline:
        for project in sorted(REFERENCE_PROJECTS):
            adapters[project] = ReferenceAdapter(project)
    return adapters


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
_ROW_DEFAULTS: dict[str, Any] = {
    "capability_id": None,
    "family": None,
    "upstream_project": "qubit",
    "upstream_commit": None,
    "upstream_path": None,
    "upstream_symbol": None,
    "upstream_hash": None,
    "qubit_path": None,
    "qubit_symbol": None,
    "disposition": "OPEN",
    "command": None,
    "fixture": None,
    "fixture_hash": None,
    "upstream_result_hash": None,
    "qubit_result_hash": None,
    "test_ids": [],
    "trace_refs": [],
    "operator_prerequisite": None,
    "rollback_ref": None,
    "normalizations": [],
}


def _coerce_rows(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        for key in ("rows", "inventory", "capabilities", "entries", "items"):
            value = doc.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        if doc.get("capability_id"):
            return [doc]
        values = [v for v in doc.values() if isinstance(v, dict) and v.get("capability_id")]
        if values:
            return values
    return []


def merge_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(_ROW_DEFAULTS)
    for key, value in raw.items():
        row[key] = value
    if not row.get("capability_id"):
        family = row.get("family") or "unknown"
        area = row.get("upstream_symbol") or row.get("upstream_path") or "unclassified"
        row["capability_id"] = f"{family}.{str(area).split(':')[0]}.unclassified"
        row["unclassifiable"] = True
    if row.get("disposition") not in DISPOSITIONS:
        row["declared_disposition_raw"] = row.get("disposition")
        row["disposition"] = "OPEN"
    if row.get("family") not in FAMILIES:
        row["unclassifiable"] = True
    for list_key in ("normalizations", "test_ids", "trace_refs"):
        if not isinstance(row.get(list_key), list):
            row[list_key] = []
    return row


def load_inventory(path: str | Path) -> list[dict[str, Any]]:
    """Load and merge inventory JSON(s) from a file or a directory tree."""
    root = Path(path)
    if not root.exists():
        return []
    if root.is_dir():
        files = sorted(p for p in root.rglob("*.json") if p.name != "MANIFEST.json")
    else:
        files = [root]
    merged: dict[str, dict[str, Any]] = {}
    for file_path in files:
        try:
            doc = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for raw in _coerce_rows(doc):
            row = merge_row(raw)
            cid = row["capability_id"]
            if cid not in merged:
                merged[cid] = row
    return [merged[cid] for cid in sorted(merged)]


def load_manifest(fixtures_dir: str | Path) -> dict[str, str]:
    """Read ``tests/fixtures/differential/MANIFEST.json`` -> {fixture_id: path}."""
    manifest_path = Path(fixtures_dir) / "MANIFEST.json"
    if not manifest_path.exists():
        return {}
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in doc.get("fixtures", []):
        if isinstance(entry, dict) and entry.get("fixture_id") and entry.get("path"):
            out[str(entry["fixture_id"])] = str(entry["path"])
    return out


def load_manifest_entries(fixtures_dir: str | Path) -> list[dict[str, Any]]:
    """Read ``MANIFEST.json`` -> ordered fixture entries (fixture_id/path/family)."""
    manifest_path = Path(fixtures_dir) / "MANIFEST.json"
    if not manifest_path.exists():
        return []
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries: list[dict[str, Any]] = []
    for entry in doc.get("fixtures", []):
        if isinstance(entry, dict) and entry.get("fixture_id") and entry.get("path"):
            entries.append(dict(entry))
    return entries


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def resolve_fixture_path(fixture_ref: str, fixtures_dir: str | Path) -> Path | None:
    ref = Path(fixture_ref)
    candidates = [ref]
    if not ref.is_absolute():
        candidates.append(Path(fixtures_dir) / ref)
        candidates.append(REPO_ROOT / ref)
        if ref.parts and ref.parts[0] != Path(fixtures_dir).name:
            candidates.append(Path(fixtures_dir) / ref.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_fixture(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("fixture must be a JSON object")
    return doc


def fixture_normalizers(row: Mapping[str, Any], fixture: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for source in (row.get("normalizations"), fixture.get("normalizers")):
        if isinstance(source, list):
            for name in source:
                if name not in names:
                    names.append(name)
    return names


def _command_spec(fixture: Mapping[str, Any]) -> str | None:
    """Human-readable command/request spec for a synthesized row."""
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return " ".join(str(a) for a in command.get("argv"))
    request = fixture.get("request")
    if isinstance(request, Mapping):
        method = str(request.get("method") or "").upper()
        path = request.get("path")
        if method or path:
            return f"{method} {path}".strip()
    return None


def synthesize_row_from_fixture(
    fixture_id: str, fixture: Mapping[str, Any], fixture_path: str | Path
) -> dict[str, Any]:
    """Build a first-class inventory row from a fixture that has no row of its own.

    The fixture is the source of truth. Its Qubit mapping supplies the real
    production caller, its command/request supplies the execution spec, and its
    normalizers are carried through. ``upstream_project`` is ``qubit`` because
    the fixture executes the Qubit surface; the verified hermes/openclaw
    mappings are retained as provenance only (there is no upstream command to
    run, so no reference adapter is invoked).
    """
    qubit = fixture.get("qubit") if isinstance(fixture.get("qubit"), Mapping) else {}
    upstream_path = None
    upstream_symbol = None
    for project in ("openclaw", "hermes"):
        mapping = fixture.get(project)
        if isinstance(mapping, Mapping) and mapping.get("path"):
            upstream_path = mapping.get("path")
            upstream_symbol = mapping.get("symbol")
            break
    raw: dict[str, Any] = {
        "capability_id": fixture_id,
        "family": fixture.get("family"),
        "upstream_project": "qubit",
        "upstream_path": upstream_path,
        "upstream_symbol": upstream_symbol,
        "qubit_path": qubit.get("path"),
        "qubit_symbol": qubit.get("caller"),
        "disposition": "OPEN",
        "command": _command_spec(fixture),
        "fixture": str(fixture_path),
        "normalizations": fixture.get("normalizers") or [],
        "test_ids": [
            "tests/test_differential_parity8u.py::test_every_manifest_fixture_is_executed"
        ],
        "trace_refs": [str(fixture_path)],
    }
    evidence = fixture.get("evidence")
    if isinstance(evidence, Mapping):
        for field in EVIDENCE_FIELDS:
            value = evidence.get(field)
            if value is not None:
                raw[field] = value
    row = merge_row(raw)
    row["fixture_derived"] = True
    return row


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _normalize_payload(
    payload: Mapping[str, Any], names: Sequence[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized, audit = normalize_and_audit(payload, list(names))
    return normalized, audit


def _refusal_detected(result: CallResult) -> bool:
    if result.exit_code in (None, 0):
        return False
    text = " ".join(part for part in (result.stdout, result.stderr) if part)
    for token in ('"refused"', '"denied"', "refused", "not permitted", "permission denied"):
        if token in text:
            return True
    parsed = _parse_json_maybe(result.stdout or "")
    if isinstance(parsed, Mapping) and str(parsed.get("state", "")).lower() in (
        "refused", "denied", "rejected"
    ):
        return True
    return False


def _execute_request(adapter: Adapter, fixture: Mapping[str, Any]) -> CallResult:
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return adapter.execute(
            {
                "kind": "cli",
                "argv": list(command.get("argv") or []),
                "stdin": command.get("stdin"),
                "env": command.get("env") or {},
                "cwd": command.get("cwd"),
            }
        )
    request = fixture.get("request")
    if isinstance(request, Mapping):
        method = str(request.get("method", "GET")).upper()
        body = request.get("body")
        declared_transport = fixture.get("transport")
        kind = str(
            declared_transport
            or request.get("kind")
            or ("http" if method else "request")
        )
        params: Any = None
        if isinstance(body, Mapping):
            params = body.get("params")
        spec: dict[str, Any] = {
            "kind": kind,
            "method": method,
            "path": request.get("path"),
            "headers": request.get("headers") or {},
            "body": body,
            "event": request.get("event"),
            "messages": request.get("messages") or fixture.get("messages") or [],
            "params": params,
            "argv": request.get("argv") or [],
            "env": request.get("env") or {},
            "base_url": fixture.get("base_url"),
            "base_url_ref": fixture.get("base_url_ref"),
            "auth": fixture.get("auth"),
            "events_expected": fixture.get("events_expected"),
            "persisted_state_schema": fixture.get("persisted_state_schema"),
        }
        sequence = fixture.get("sequence")
        if isinstance(sequence, list) and sequence:
            spec["sequence"] = sequence
        return adapter.execute(spec)
    raise ValueError("fixture has neither command.argv nor request")


def _fixture_argv(fixture: Mapping[str, Any]) -> list[str]:
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return [str(a) for a in command.get("argv")]
    request = fixture.get("request")
    if isinstance(request, Mapping):
        return [str(a) for a in (request.get("argv") or [])]
    return []


def _new_result(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["declared_disposition"] = row.get("disposition", "OPEN")
    result["executed"] = False
    result["fixture_present"] = False
    result["qubit_executed"] = False
    result["reference_executed"] = False
    result["adapter_mock"] = False
    result["refused"] = False
    result["unsafe_normalizers"] = []
    result["visual_only"] = False
    result["voice_mismatch"] = False
    result["persistence_diff"] = False
    result["secret_in_artifact"] = False
    result["operator_prerequisite"] = result.get("operator_prerequisite")
    result["production_caller"] = row.get("qubit_symbol")
    result["diagnostics"] = []
    result["fixture_hash"] = None
    result["upstream_result_hash"] = None
    result["qubit_result_hash"] = None
    result["qubit_raw_result_hash"] = None
    result["reference_result_hash"] = None
    result["unclassifiable"] = bool(row.get("unclassifiable"))
    for field in EVIDENCE_FIELDS:
        result.setdefault(field, None)
    return result


def _add_diags(result: dict[str, Any], diags: Sequence[Diagnostic]) -> None:
    for diag in diags:
        result["diagnostics"].append(diag.to_dict())


def run_inventory(
    rows: Sequence[Mapping[str, Any]],
    fixtures_dir: str | Path = DEFAULT_FIXTURES_DIR,
    adapters: Mapping[str, Adapter] | None = None,
    *,
    row_ids: Sequence[str] | None = None,
    families: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Execute every inventory row AND every manifest fixture; never PASS unexecuted.

    Inventory-driven rows are run first. Then each ``MANIFEST.json`` fixture is
    run: if an inventory row already carries the fixture's ``capability_id`` the
    fixture binds to it; otherwise a first-class row is synthesized from the
    fixture's own fields. This makes an ignored fixture impossible — every
    manifest fixture yields exactly one result with a non-OPEN disposition
    (PASS/FAIL/SAFE_EQUIVALENT/OPERATOR_ONLY) or is explicitly unclassifiable.
    """
    if adapters is None:
        adapters = default_adapters()
    manifest = load_manifest(fixtures_dir)
    entries = load_manifest_entries(fixtures_dir)
    wanted_ids = {str(x) for x in row_ids} if row_ids is not None else None
    wanted_families = {str(x) for x in families} if families is not None else None

    results: list[dict[str, Any]] = []
    covered: set[str] = set()
    for row in rows:
        results.append(_run_row(row, fixtures_dir, adapters, manifest))
        covered.add(str(row.get("capability_id")))

    for entry in entries:
        fixture_id = str(entry["fixture_id"])
        if fixture_id in covered:
            continue
        if wanted_ids is not None and fixture_id not in wanted_ids:
            continue
        if wanted_families is not None and str(entry.get("family")) not in wanted_families:
            continue
        results.append(_run_fixture_entry(entry, fixtures_dir, adapters, manifest))
        covered.add(fixture_id)
    return results


def _fixture_placeholder_result(
    entry: Mapping[str, Any], fixture_present: bool, kind: str, detail: str
) -> dict[str, Any]:
    """Never silently drop a manifest fixture: emit a fail-closed result."""
    row = merge_row(
        {
            "capability_id": str(entry.get("fixture_id")),
            "family": entry.get("family"),
            "disposition": "OPEN",
            "fixture": str(entry.get("path")),
        }
    )
    result = _new_result(row)
    result["fixture_present"] = fixture_present
    result["unclassifiable"] = True
    result["disposition"] = "OPEN"
    result["manifest_fixture"] = True
    _add_diags(result, [_diag("$.fixture", kind, detail)])
    return result


def _run_fixture_entry(
    entry: Mapping[str, Any],
    fixtures_dir: str | Path,
    adapters: Mapping[str, Adapter],
    manifest: Mapping[str, str],
) -> dict[str, Any]:
    """Execute one manifest fixture, synthesizing a row when none exists."""
    fixture_id = str(entry["fixture_id"])
    fixture_ref = str(entry["path"])
    fixture_path = resolve_fixture_path(fixture_ref, fixtures_dir)
    if fixture_path is None:
        return _fixture_placeholder_result(
            entry, False, "fixture_not_found",
            f"manifest fixture missing on disk: {fixture_ref}",
        )
    try:
        fixture = load_fixture(fixture_path)
    except (OSError, ValueError) as exc:
        return _fixture_placeholder_result(
            entry, True, "fixture_parse_error",
            f"manifest fixture could not be parsed: {exc}",
        )
    row = synthesize_row_from_fixture(fixture_id, fixture, fixture_path)
    result = _run_row(row, fixtures_dir, adapters, manifest)
    result["manifest_fixture"] = True
    return result


def _classify_unavailable(
    row: Mapping[str, Any], result: dict[str, Any], prerequisite: str
) -> dict[str, Any]:
    result["disposition"] = "OPERATOR_ONLY"
    result["operator_prerequisite"] = prerequisite
    result["executed"] = False
    return result


def _run_row(
    row: Mapping[str, Any],
    fixtures_dir: str | Path,
    adapters: Mapping[str, Adapter],
    manifest: Mapping[str, str],
) -> dict[str, Any]:
    result = _new_result(row)
    declared = result["declared_disposition"]

    if row.get("unclassifiable"):
        result["disposition"] = "OPEN"
        result["unclassifiable"] = True
        return result

    fixture_ref = row.get("fixture") or manifest.get(str(row.get("capability_id")))
    if not fixture_ref:
        result["disposition"] = declared if declared in ("OPEN", "OPERATOR_ONLY") else "OPEN"
        return result

    fixture_path = resolve_fixture_path(str(fixture_ref), fixtures_dir)
    if fixture_path is None:
        _classify_unavailable(
            row, result, f"fixture not found: {fixture_ref} (author the fixture first)"
        )
        return result

    result["fixture_present"] = True
    try:
        fixture = load_fixture(fixture_path)
        result["fixture_hash"] = file_hash(fixture_path)
    except (OSError, ValueError) as exc:
        result["disposition"] = "FAIL"
        _add_diags(result, [_diag("$.fixture", "fixture_parse_error", str(exc))])
        return result

    qubit_meta = fixture.get("qubit") if isinstance(fixture.get("qubit"), Mapping) else {}
    if qubit_meta.get("caller"):
        result["production_caller"] = qubit_meta.get("caller")

    # Plan §8/§15 evidence: row fields win, then fixture top-level, then the
    # fixture's nested ``evidence`` block. Keys already exist (defaults None).
    fixture_evidence = (
        fixture.get("evidence") if isinstance(fixture.get("evidence"), Mapping) else {}
    )
    for field in EVIDENCE_FIELDS:
        if result.get(field) is not None:
            continue
        value = row.get(field)
        if value is None:
            value = fixture.get(field)
        if value is None and isinstance(fixture_evidence, Mapping):
            value = fixture_evidence.get(field)
        if value is not None:
            result[field] = value

    evidence_notes = (
        fixture_evidence.get("evidence_notes")
        if isinstance(fixture_evidence.get("evidence_notes"), Mapping)
        else {}
    )

    normalizers = fixture_normalizers(row, fixture)
    argv = _fixture_argv(fixture)
    env_values = []
    command = fixture.get("command")
    if isinstance(command, Mapping) and isinstance(command.get("env"), Mapping):
        env_values = list(command["env"].values())

    secret_paths = sorted(
        set(
            scan_secrets(fixture)
            + [f"$.command.argv[{i}]" for i, a in enumerate(argv) if contains_secret(a)]
            + [f"$.command.env.{k}" for k, v in (command or {}).get("env", {}).items()
               if isinstance(v, str) and contains_secret(v)]
        )
    )
    if secret_paths:
        result["secret_in_artifact"] = True
        result["disposition"] = "FAIL"
        _add_diags(
            result,
            [_diag(p, "secret_in_artifact", "secret-shaped value present; not executed")
             for p in secret_paths],
        )
        return result

    try:
        result["unsafe_normalizers"] = []
        normalized_names = normalizers
        for name in normalizers:
            if name not in ALLOWED_NORMALIZERS:
                raise NormalizerError(f"unknown normalizer: {name!r}")
    except NormalizerError as exc:
        result["disposition"] = "FAIL"
        result["unsafe_normalizers"] = [str(exc)]
        _add_diags(result, [_diag("$.normalizers", "unsafe_normalizer", str(exc))])
        return result

    qubit_adapter = adapters.get("qubit")
    if qubit_adapter is None or not qubit_adapter.available():
        _classify_unavailable(row, result, "qubit adapter/binary unavailable (voicebridge CLI)")
        return result

    try:
        qubit_call = _execute_request(qubit_adapter, fixture)
    except NotImplementedError as exc:
        _classify_unavailable(row, result, f"qubit transport unavailable: {exc}")
        return result
    except TransportUnavailable as exc:
        _classify_unavailable(row, result, str(exc))
        return result
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        result["disposition"] = "FAIL"
        _add_diags(result, [_diag("$.qubit", "adapter_error", str(exc)[:300])])
        return result

    result["qubit_executed"] = True
    result["executed"] = True
    result["duration_ms"] = qubit_call.duration_ms
    result["transport"] = qubit_call.transport
    result["http_status"] = qubit_call.http_status
    qubit_payload = qubit_call.to_payload()
    result["qubit_payload"] = qubit_payload
    result["qubit_raw_result_hash"] = result_hash(qubit_payload)
    if result.get("raw_hash") is None:
        # raw_hash is the byte-comparable proof of the un-normalized result.
        result["raw_hash"] = result["qubit_raw_result_hash"]
    if qubit_call.meta.get("adapter"):
        result["adapter_mock"] = bool(qubit_call.meta.get("mock", False))

    qubit_norm, qubit_audit = _normalize_payload(qubit_payload, normalized_names)
    expected = fixture.get("expected") if isinstance(fixture.get("expected"), Mapping) else {}
    expected_norm, expected_audit = _normalize_payload(expected, normalized_names)

    unsafe_events = (
        detect_unsafe_normalizers(qubit_audit)
        + detect_unsafe_normalizers(expected_audit)
    )
    asymmetric = asymmetric_normalizers(expected, qubit_payload, normalized_names)
    if unsafe_events or asymmetric:
        result["unsafe_normalizers"] = sorted(
            set(str(u.get("path")) for u in unsafe_events) | set(asymmetric)
        )
    result["qubit_result_hash"] = result_hash(qubit_norm)
    result["refused"] = _refusal_detected(qubit_call)

    diags = compare_expected_actual(
        expected, qubit_payload, "$", normalizers=normalized_names
    )
    diags += _family_diagnostics(str(row.get("family")), expected_norm, qubit_norm)

    # --- tri-way reference execution (declared in fixture.references) ------ #
    references = (
        fixture.get("references") if isinstance(fixture.get("references"), Mapping) else {}
    )
    reference_projects = [p for p in sorted(references) if p in REFERENCE_PROJECTS]
    reference_info: dict[str, Any] = {}
    reference_hashes: dict[str, str] = {}
    reference_unavailable: list[str] = []
    unpinned_references: list[str] = []
    reference_executed = False

    for project in reference_projects:
        spec = references.get(project)
        adapter = adapters.get(project)
        info: dict[str, Any] = {
            "project": project,
            "executed": False,
            "binary": getattr(adapter, "binary", None),
        }
        if not isinstance(spec, Mapping):
            info["reason"] = "invalid reference spec (not an object)"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        ref_kind = str(spec.get("kind") or spec.get("transport") or "")
        if not ref_kind:
            if spec.get("argv"):
                ref_kind = "cli"
            elif spec.get("request") or spec.get("path"):
                ref_kind = "http"
        if not ref_kind or ref_kind not in ("cli", "http", "sse", "ws", "rpc"):
            info["reason"] = "invalid reference spec (missing argv/transport)"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        if adapter is None or not adapter.available():
            info["reason"] = (
                f"{project} binary/URL unavailable "
                f"(set QUBIT_DIFFERENTIAL_{project.upper()}_BIN or _URL)"
            )
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        request_spec: dict[str, Any] = {"kind": ref_kind, "env": spec.get("env") or {}}
        if ref_kind == "cli":
            request_spec["argv"] = list(spec.get("argv") or [])
        else:
            nested = spec.get("request") if isinstance(spec.get("request"), Mapping) else spec
            request_spec.update(
                {
                    "method": nested.get("method") or spec.get("method") or "GET",
                    "path": nested.get("path") or spec.get("path"),
                    "headers": nested.get("headers") or spec.get("headers") or {},
                    "body": nested.get("body") if "body" in nested else spec.get("body"),
                    "messages": nested.get("messages") or spec.get("messages") or [],
                    "base_url": spec.get("base_url"),
                    "base_url_ref": spec.get("base_url_ref"),
                    "auth": spec.get("auth"),
                    "sequence": spec.get("sequence"),
                }
            )
        try:
            reference_call = adapter.execute(request_spec)
        except TransportUnavailable as exc:
            info["reason"] = f"reference transport unavailable: {exc}"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        except (RuntimeError, OSError, subprocess.SubprocessError, NotImplementedError) as exc:
            info["reason"] = f"reference execution failed: {exc}"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        reference_payload = reference_call.to_payload()
        reference_norm, reference_audit = _normalize_payload(
            reference_payload, normalized_names
        )
        reference_unsafe = detect_unsafe_normalizers(reference_audit)
        reference_asym = asymmetric_normalizers(
            expected, reference_payload, normalized_names
        )
        if reference_unsafe or reference_asym:
            result["unsafe_normalizers"] = sorted(
                set(result.get("unsafe_normalizers") or [])
                | {str(u.get("path")) for u in reference_unsafe}
                | set(reference_asym)
            )
        reference_hash = result_hash(reference_norm)
        reference_hashes[project] = reference_hash
        reference_executed = True
        version = _probe_version(adapter)
        commit = _resolve_reference_commit(project, adapter, version)
        pinned = _reference_is_pinned(project, commit)
        if not pinned:
            unpinned_references.append(project)
        info.update(
            {
                "executed": True,
                "reference_version": version,
                "reference_commit": commit,
                "reference_pinned": pinned,
                "argv": list(spec.get("argv") or []),
                "result_hash": reference_hash,
                "raw_result_hash": result_hash(reference_payload),
            }
        )
        reference_info[project] = info
        result.setdefault("reference_payloads", {})[project] = reference_payload
        diags += compare_expected_actual(
            expected, reference_payload, "$", normalizers=normalized_names
        )

    result["references"] = reference_info
    result["unpinned_references"] = unpinned_references
    if reference_hashes:
        combined = (
            next(iter(reference_hashes.values()))
            if len(reference_hashes) == 1
            else result_hash(reference_hashes)
        )
        result["reference_result_hash"] = combined
        result["upstream_result_hash"] = combined
    result["reference_executed"] = reference_executed

    # --- disposition (fail closed) ---------------------------------------- #
    if declared == "FAIL":
        result["disposition"] = "FAIL"
    elif diags or result["unsafe_normalizers"] or result["secret_in_artifact"]:
        result["disposition"] = "SAFE_EQUIVALENT" if declared == "SAFE_EQUIVALENT" else "FAIL"
    elif declared == "SAFE_EQUIVALENT":
        result["disposition"] = "SAFE_EQUIVALENT"
    elif not reference_executed:
        result["disposition"] = "OPERATOR_ONLY"
        detail = (
            ", ".join(reference_unavailable)
            if reference_unavailable
            else "no references declared for this surface"
        )
        result["operator_prerequisite"] = (
            f"no upstream reference executed ({detail}); set "
            "QUBIT_DIFFERENTIAL_<PROJECT>_BIN or declare fixture.references"
        )
    elif unpinned_references:
        # A floating upstream can never prove parity: record a typed
        # safe-equivalent instead of a false PASS.
        result["disposition"] = "SAFE_EQUIVALENT"
        result["disposition_note"] = (
            "typed safe-equivalent: reference(s) unpinned ("
            + ", ".join(unpinned_references)
            + "); parity cannot be claimed against a floating upstream"
        )
    else:
        result["disposition"] = "PASS"

    # v1.1 evidence requirement: a claimed success must carry every evidence
    # field (or an explicit non-applicability note) so a PASS can never be
    # counted without evidence.
    if result["disposition"] in SUCCESS_DISPOSITIONS:
        missing_evidence = missing_evidence_fields(result, evidence_notes)
        if missing_evidence:
            result["missing_evidence"] = missing_evidence
            result["disposition"] = "FAIL"
            _add_diags(
                result,
                [_diag("$.evidence", "missing_evidence",
                       "PASS/SAFE_EQUIVALENT row lacks evidence: "
                       + ", ".join(missing_evidence))],
            )

    if any(str(d.path).startswith(("$.persisted_state", "$.receipts")) for d in diags) or any(
        d.kind in ("persistence", "recovery") for d in diags
    ):
        result["persistence_diff"] = True
    if str(row.get("family")) == "voice":
        mismatched = [
            d for d in diags
            if str(d.path).startswith(("$.voice_operation", "$.resolved_identity", "$.confirmation"))
        ]
        if mismatched and result["disposition"] == "PASS":
            result["voice_mismatch"] = True
    if str(row.get("family")) in VISUAL_FAMILIES and not qubit_norm.get("backend_operation"):
        result["visual_only"] = True

    _add_diags(result, diags)
    return result


# --------------------------------------------------------------------------- #
# Fail-closed gates
# --------------------------------------------------------------------------- #
def _index_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(r.get("capability_id")): r for r in results}


def _gate(
    name: str, triggering: list[str], detail: str
) -> dict[str, Any]:
    return {"gate": name, "ok": not triggering, "rows": triggering, "detail": detail}


def apply_fail_closed_gates(
    rows: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Re-evaluate every required fail-closed gate from rows + results.

    Gates are evaluated over the UNION of inventory rows and results, so a
    result with no inventory row (a fixture synthesized by
    :func:`run_inventory`) is still gated instead of being silently ignored.
    """
    by_id = _index_results(results)
    gates: list[dict[str, Any]] = []

    entries: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    seen_ids: set[str] = set()
    for row in rows:
        cid = str(row.get("capability_id"))
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        entries.append((row, by_id.get(cid, row)))
    for res in results:
        cid = str(res.get("capability_id"))
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        entries.append((res, res))

    unexecuted: list[str] = []
    missing_caller: list[str] = []
    skipped: list[str] = []
    mocked: list[str] = []
    refusal: list[str] = []
    unsafe: list[str] = []
    visual: list[str] = []
    voice: list[str] = []
    persistence: list[str] = []
    secrets: list[str] = []
    undisposed: list[str] = []
    open_rows: list[str] = []
    no_reference_pass: list[str] = []
    unpinned_pass: list[str] = []

    # Explicit coverage: every INPUT row must have a result object at all.
    uncovered: list[str] = []
    covered_ids: set[str] = set()
    for row in rows:
        cid = str(row.get("capability_id"))
        if cid in covered_ids:
            continue
        covered_ids.add(cid)
        if cid not in by_id:
            uncovered.append(cid)

    for row, res in entries:
        cid = str(row.get("capability_id"))
        disposition = str(res.get("disposition") or "OPEN")
        executed = bool(res.get("executed"))

        if disposition == "OPEN":
            open_rows.append(cid)
        if disposition == "PASS" and res.get("unpinned_references"):
            unpinned_pass.append(cid)
        if disposition in ("PASS", "SAFE_EQUIVALENT") and not res.get("reference_executed"):
            no_reference_pass.append(cid)
        if disposition in ("PASS", "SAFE_EQUIVALENT") and not executed:
            unexecuted.append(cid)
        if disposition in ("PASS", "SAFE_EQUIVALENT") and not (
            res.get("production_caller") or row.get("qubit_symbol")
        ):
            missing_caller.append(cid)
        if res.get("fixture_present") and not executed and disposition != "OPERATOR_ONLY":
            skipped.append(cid)
        if res.get("adapter_mock") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            mocked.append(cid)
        if res.get("refused") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            refusal.append(cid)
        if res.get("unsafe_normalizers"):
            unsafe.append(cid)
        if res.get("visual_only") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            visual.append(cid)
        if res.get("voice_mismatch"):
            voice.append(cid)
        if res.get("persistence_diff") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            persistence.append(cid)
        if res.get("secret_in_artifact") or scan_secrets(res.get("qubit_payload")):
            secrets.append(cid)
        if is_openclaw_delta_row(row) and not _has_qubit_disposition_and_proof(row, res):
            undisposed.append(cid)

    gates.append(_gate("unexecuted_row", unexecuted,
                       "row claims PASS/SAFE_EQUIVALENT without an executed result"))
    gates.append(_gate("missing_production_caller", missing_caller,
                       "row claims PASS/SAFE_EQUIVALENT without a Qubit production caller"))
    gates.append(_gate("skipped_fixture", skipped,
                       "fixture present but execution was skipped without OPERATOR_ONLY"))
    gates.append(_gate("mock_replaced_real_path", mocked,
                       "a mock adapter result was counted as PASS/SAFE_EQUIVALENT"))
    gates.append(_gate("refusal_counted_as_success", refusal,
                       "a typed refusal/denial was counted as PASS/SAFE_EQUIVALENT"))
    gates.append(_gate("unsafe_normalizer", unsafe,
                       "a normalizer collapsed an out-of-shape difference"))
    gates.append(_gate("visual_only_control", visual,
                       "a visual control reached no canonical backend operation"))
    gates.append(_gate("voice_backend_mismatch", voice,
                       "voice resolved a different canonical operation than the reference"))
    gates.append(_gate("persistence_or_recovery_diff", persistence,
                       "persisted state or recovery semantics diverged"))
    gates.append(_gate("secret_in_artifact", secrets,
                       "secret-shaped value present in an artifact"))
    gates.append(_gate("openclaw_undisposed_addition", undisposed,
                       "OpenClaw latest-release/default-branch delta row lacks a "
                       "Qubit disposition and proof"))
    gates.append(_gate("unexecuted_open_row", open_rows,
                       "row was left OPEN (not executed and not classified "
                       "OPERATOR_ONLY/PASS/FAIL) — fail closed"))
    gates.append(_gate("pass_without_reference", no_reference_pass,
                       "row claims PASS/SAFE_EQUIVALENT without any executed "
                       "upstream reference"))
    gates.append(_gate("unpinned_reference_pass", unpinned_pass,
                       "row claims PASS while an executed reference is not the "
                       "contractual pin (floating upstream)"))
    gates.append(_gate("inventory_coverage", uncovered,
                       "input row has no result object at all (silently unexecuted)"))
    return gates


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def build_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {name.lower(): 0 for name in DISPOSITIONS}
    for row in rows:
        disposition = str(row.get("disposition") or "OPEN")
        key = disposition.lower()
        if key in counts:
            counts[key] += 1
    return {
        "total": len(rows),
        "pass": counts["pass"],
        "fail": counts["fail"],
        "safe_equivalent": counts["safe_equivalent"],
        "operator_only": counts["operator_only"],
        "open": counts["open"],
    }


def build_verdict(rows: Sequence[Mapping[str, Any]], gates: Sequence[Mapping[str, Any]]) -> str:
    if any(not g.get("ok") for g in gates):
        return "INCOMPLETE"
    if any(str(r.get("disposition")) == "FAIL" for r in rows):
        return "INCOMPLETE"
    if any(str(r.get("disposition")) in ("OPERATOR_ONLY", "OPEN") for r in rows):
        return "VERIFIED WITH EXPLICIT LIMITATION"
    return "VERIFIED"


def build_report(
    rows: Sequence[Mapping[str, Any]], gates: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "runner_version": RUNNER_VERSION,
        "rows": list(rows),
        "totals": build_totals(rows),
        "gates": list(gates),
        "verdict": build_verdict(rows, gates),
    }


def report_exit_code(report: Mapping[str, Any]) -> int:
    if report.get("internal_error"):
        return EXIT_INTERNAL
    rows = report.get("rows") or []
    # A failed fail-closed gate (e.g. unexecuted_open_row) is a definitive FAIL
    # and outranks the weaker "could not classify" signal: an OPEN row must make
    # the run fail rather than merely report unclassifiable.
    if any(not g.get("ok") for g in report.get("gates") or []):
        return EXIT_FAIL
    if any(str(r.get("disposition")) == "FAIL" for r in rows):
        return EXIT_FAIL
    if any(r.get("unclassifiable") for r in rows):
        return EXIT_UNCLASSIFIABLE
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    if target.parent and str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_inventory(args: argparse.Namespace) -> int:
    source = Path(args.source or DEFAULT_INVENTORY_DIR)
    rows = load_inventory(source)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "runner_version": RUNNER_VERSION,
        "inventory_source": str(source),
        "rows": rows,
        "totals": build_totals(rows),
    }
    _write_json(args.out, payload)
    print(f"inventory: {len(rows)} rows -> {args.out}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    if not inventory_path.exists():
        print(f"error: inventory not found: {inventory_path}", file=sys.stderr)
        return EXIT_USAGE
    rows = load_inventory(inventory_path)
    if args.family:
        rows = [r for r in rows if str(r.get("family")) == args.family]
    if args.row:
        rows = [r for r in rows if str(r.get("capability_id")) == args.row]
    adapters = default_adapters()
    results = run_inventory(
        rows, args.fixtures_dir, adapters,
        row_ids=[args.row] if args.row else None,
        families=[args.family] if args.family else None,
    )
    gates = apply_fail_closed_gates(rows, results)
    report = build_report(results, gates)
    _write_json(args.out, report)
    totals = report["totals"]
    print(
        f"run: {totals['total']} rows, pass={totals['pass']} fail={totals['fail']} "
        f"safe_equivalent={totals['safe_equivalent']} "
        f"operator_only={totals['operator_only']} open={totals['open']}; "
        f"verdict={report['verdict']}"
    )
    return report_exit_code(report)


def cmd_verify(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    if not report_path.exists():
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return EXIT_USAGE
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        print("error: malformed report", file=sys.stderr)
        return EXIT_INTERNAL
    rows = doc["rows"]
    gates = apply_fail_closed_gates(rows, rows)
    rebuilt = build_report(rows, gates)
    recomputed = {gate["gate"]: gate["ok"] for gate in gates}
    reported = {gate.get("gate"): gate.get("ok") for gate in doc.get("gates") or []}
    if recomputed != reported:
        print("error: report gates do not match recomputed gates", file=sys.stderr)
        return EXIT_FAIL
    if doc.get("totals") != rebuilt["totals"]:
        print("error: report totals do not match recomputed totals", file=sys.stderr)
        return EXIT_FAIL
    if doc.get("verdict") != rebuilt["verdict"]:
        print("error: report verdict does not match recomputed verdict", file=sys.stderr)
        return EXIT_FAIL
    failed = [g["gate"] for g in gates if not g["ok"]]
    print(f"verify: verdict={rebuilt['verdict']} failed_gates={failed}")
    return report_exit_code(rebuilt)


def cmd_selftest(args: argparse.Namespace) -> int:
    return selftest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="differential_parity",
        description="Deterministic differential parity runner (fail-closed).",
    )
    parser.add_argument(
        "--version", action="version", version=f"{GENERATED_BY} {RUNNER_VERSION} (schema {SCHEMA_VERSION})"
    )
    sub = parser.add_subparsers(dest="command")

    p_inv = sub.add_parser("inventory", help="merge inventory JSONs into the Row schema")
    p_inv.add_argument("--out", required=True, help="output JSON path")
    p_inv.add_argument("--source", default=None, help="inventory file or directory")
    p_inv.set_defaults(func=cmd_inventory)

    p_run = sub.add_parser("run", help="execute fixtures and compare")
    p_run.add_argument("--inventory", required=True)
    p_run.add_argument("--fixtures-dir", default=DEFAULT_FIXTURES_DIR)
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--family", default=None)
    p_run.add_argument("--row", default=None)
    p_run.set_defaults(func=cmd_run)

    p_verify = sub.add_parser("verify", help="re-check a report's gates")
    p_verify.add_argument("--report", required=True)
    p_verify.set_defaults(func=cmd_verify)

    p_self = sub.add_parser("selftest", help="run internal self-checks")
    p_self.set_defaults(func=cmd_selftest)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in ("--version", "-V"):
        print(f"{GENERATED_BY} {RUNNER_VERSION} (schema {SCHEMA_VERSION})")
        return EXIT_OK
    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        return int(exc.code or EXIT_USAGE)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except NormalizerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - last-resort fail-closed boundary
        print(f"internal error: {redact_text(str(exc))}", file=sys.stderr)
        return EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
class _Checker:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, name: str, condition: Any, detail: str = "") -> bool:
        self.checks += 1
        ok = bool(condition)
        if not ok:
            self.failures += 1
        suffix = f": {detail}" if (not ok and detail) else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        return ok


def _baseline_gate_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    row = merge_row(
        {
            "capability_id": "cli.status.list",
            "family": "cli",
            "upstream_project": "qubit",
            "qubit_path": "voicebridge/cli.py:1",
            "qubit_symbol": "main",
            "disposition": "PASS",
            "fixture": "cli/status.json",
        }
    )
    result = _new_result(row)
    result["executed"] = True
    result["fixture_present"] = True
    result["disposition"] = "PASS"
    result["production_caller"] = "main"
    result["reference_executed"] = True
    return row, result


def selftest() -> int:
    """Exercise allowlist exactness, hash determinism, path precision, gates."""
    chk = _Checker()

    expected_names = {
        "timestamp", "uuid", "temp_path", "pid", "session_token",
        "generated_hash", "product_branding",
    }
    chk.check(
        "normalizer_allowlist_is_exact",
        set(ALLOWED_NORMALIZERS) == expected_names,
        f"got {sorted(ALLOWED_NORMALIZERS)}",
    )
    try:
        normalize({"x": 1}, ["bogus"])
        chk.check("unknown_normalizer_raises", False, "no error raised")
    except NormalizerError:
        chk.check("unknown_normalizer_raises", True)

    samples: dict[str, tuple[Any, Any, Any, Any]] = {
        "timestamp": ("2024-01-02T03:04:05Z", "name", "<timestamp>", "hello"),
        "uuid": ("12345678-1234-1234-1234-123456789abc", "id", "<uuid>", "hello"),
        "temp_path": ("/tmp/foo/bar.txt", "path", "<temp_path>", "relative/path"),
        "pid": (1234, "pid", "<pid>", "not-a-pid"),
        "session_token": ("abcdef123456", "token", "<session_token>", "short"),
        "generated_hash": ("a" * 64, "hash", "<generated_hash>", "xyz"),
        "product_branding": ("Hermes Agent", "name", "<product> Agent", "plain"),
    }
    for name, (value, key, want, other) in samples.items():
        fn = ALLOWED_NORMALIZERS[name]
        chk.check(f"normalizer_{name}_transforms", fn(value, key) == want, f"got {fn(value, key)!r}")
        chk.check(f"normalizer_{name}_narrow", fn(other, "unrelated") == other)

    normalized = normalize({"created_at": "2024-01-02T03:04:05Z", "name": "hermes"},
                           ["timestamp", "product_branding"])
    chk.check(
        "normalize_walks_nested",
        normalized == {"created_at": "<timestamp>", "name": "<product>"},
        f"got {normalized}",
    )

    payload = {"b": 1, "a": [1, 2], "u": "\u00e9"}
    direct = "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    chk.check("result_hash_matches_contract", result_hash(payload) == direct, result_hash(payload))
    chk.check(
        "result_hash_key_order_independent",
        result_hash({"b": 1, "a": 2}) == result_hash({"a": 2, "b": 1}),
    )

    diags = compare_expected_actual(
        {"a": {"b": [1, {"c": 2}]}}, {"a": {"b": [1, {"c": 3}]}}
    )
    chk.check(
        "diagnostic_path_precision",
        len(diags) == 1 and diags[0].path == "$.a.b[1].c" and diags[0].kind == "value",
        f"got {[(d.path, d.kind) for d in diags]}",
    )
    selector_diags = compare_expected_actual({"#submit": {"x": 1}}, {"#submit": {"x": 2}})
    chk.check(
        "diagnostic_selector_path",
        len(selector_diags) == 1 and selector_diags[0].path == '$["#submit"].x',
        f"got {[d.path for d in selector_diags]}",
    )
    schema_diags = compare_expected_actual(
        {"json_schema": {"id": 1}}, {"json": {"id": 2, "extra": 1}}
    )
    chk.check(
        "schema_template_ignores_extras",
        [d.path for d in schema_diags] == ["$.json_schema.id"],
        f"got {[d.path for d in schema_diags]}",
    )
    chk.check(
        "header_nondeterminism_ignored",
        compare_expected_actual(
            {"headers": {"Date": "x", "Content-Type": "application/json"}},
            {"headers": {"date": "y", "content-type": "application/json"}},
        ) == [],
    )
    chk.check(
        "scope_sets_compare",
        compare_expected_actual({"scopes": ["a", "b"]}, {"scopes": ["b", "a"]}) == [],
    )
    chk.check(
        "envelope_subset_ignores_transport_fields",
        compare_expected_actual(
            {"exit_code": 0},
            {"exit_code": 0, "stdout": "usage", "stderr": "", "duration": 0.1},
        ) == [],
    )
    chk.check(
        "closed_object_flags_extras",
        [d.path for d in compare_expected_actual(
            {"closed": True, "exit_code": 0},
            {"exit_code": 0, "stdout": "usage"},
        )] == ["$.stdout"],
    )
    symmetric = compare_expected_actual(
        {"created_at": "2024-01-02T03:04:05Z"},
        {"created_at": "2025-06-07T08:09:10Z"},
        normalizers=["timestamp"],
    )
    chk.check("normalizers_applied_symmetrically", symmetric == [], f"got {symmetric}")
    chk.check(
        "asymmetric_normalizer_flagged",
        asymmetric_normalizers(
            {"created_at": "2024-01-02T03:04:05Z"},
            {"created_at": "not-a-timestamp"},
            ["timestamp"],
        ) == ["$.created_at"],
    )

    chk.check(
        "response_headers_ignore_nondeterministic",
        compare_response_headers(
            {"Date": "Mon", "Server": "uvicorn", "Content-Type": "application/json"},
            {"date": "Tue", "server": "hypercorn", "content-type": "application/json"},
        ) == [],
    )
    header_diags = compare_response_headers(
        {"Content-Type": "application/json"}, {"content-type": "text/plain"}
    )
    chk.check(
        "response_headers_flag_deterministic",
        len(header_diags) == 1 and "content-type" in header_diags[0].path,
        f"got {[d.path for d in header_diags]}",
    )
    chk.check(
        "help_text_requires_canonical_command",
        check_help_text(f"usage: {CANONICAL_QUBIT_COMMAND} [OPTIONS]") == []
        and check_help_text("usage: unrelated-tool [OPTIONS]") != [],
    )
    chk.check(
        "openclaw_delta_detection",
        is_openclaw_delta_row({"upstream_project": "openclaw", "upstream_commit": "main"})
        and not is_openclaw_delta_row(
            {"upstream_project": "openclaw", "upstream_commit": "a" * 40}
        )
        and not is_openclaw_delta_row(
            {"upstream_project": "hermes", "upstream_commit": "main"}
        ),
    )
    evidence_probe = _new_result(_baseline_gate_inputs()[0])
    chk.check(
        "result_has_all_evidence_keys",
        missing_evidence_keys(evidence_probe) == [],
        f"missing {missing_evidence_keys(evidence_probe)}",
    )
    evidence_pass = dict(evidence_probe)
    evidence_pass["disposition"] = "PASS"
    for field in EVIDENCE_FIELDS:
        evidence_pass[field] = [] if field == "error_codes" else f"<{field}>"
    chk.check(
        "success_evidence_complete_ok",
        missing_evidence_fields(evidence_pass) == [],
    )
    evidence_missing = dict(evidence_pass)
    evidence_missing["response_headers"] = None
    chk.check(
        "success_missing_evidence_flagged",
        missing_evidence_fields(evidence_missing) == ["response_headers"],
        f"got {missing_evidence_fields(evidence_missing)}",
    )
    evidence_open = dict(evidence_missing)
    evidence_open["disposition"] = "OPEN"
    chk.check(
        "open_row_evidence_exempt",
        missing_evidence_fields(evidence_open) == [],
    )

    audit_obj = {"id": "alice", "ts": "2024-01-02T03:04:05Z"}
    _, audit = normalize_and_audit(audit_obj, ["timestamp"])
    chk.check("safe_normalizer_no_unsafe", detect_unsafe_normalizers(audit) == [])
    evil_registry = dict(ALLOWED_NORMALIZERS)
    evil_registry["timestamp"] = lambda value, key=None: "<collapsed>"
    _, evil_audit = normalize_and_audit({"id": "alice"}, ["timestamp"], registry=evil_registry)
    unsafe = detect_unsafe_normalizers(evil_audit)
    chk.check(
        "unsafe_normalizer_detected",
        len(unsafe) == 1 and unsafe[0]["path"] == "$.id",
        f"got {[u.get('path') for u in unsafe]}",
    )

    # Build the fake key by concatenation so this test harness itself does not
    # contain a secret-shaped literal (which would fail the release secret
    # scan for a file that only *demonstrates* redaction).
    _fake_aws_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    secrets = scan_secrets({"api_key": _fake_aws_key})
    chk.check("secret_scan_finds", secrets == ["$.api_key"], f"got {secrets}")
    chk.check(
        "secret_redacted_in_detail",
        _fake_aws_key not in redact_text("key=" + _fake_aws_key),
    )

    row, result = _baseline_gate_inputs()
    clean_gates = apply_fail_closed_gates([row], [result])
    chk.check(
        "gate_names_exact",
        [g["gate"] for g in clean_gates] == list(GATE_NAMES),
        f"got {[g['gate'] for g in clean_gates]}",
    )
    chk.check(
        "clean_row_all_gates_ok",
        all(g["ok"] for g in clean_gates),
        f"failing: {[g['gate'] for g in clean_gates if not g['ok']]}",
    )

    triggers: dict[str, dict[str, Any]] = {
        "unexecuted_row": {"executed": False, "fixture_present": False},
        "missing_production_caller": {"production_caller": None, "qubit_symbol": None},
        "skipped_fixture": {"executed": False, "fixture_present": True, "disposition": "OPEN"},
        "mock_replaced_real_path": {"adapter_mock": True, "disposition": "PASS"},
        "refusal_counted_as_success": {"refused": True, "disposition": "PASS"},
        "unsafe_normalizer": {"unsafe_normalizers": ["$.x"]},
        "visual_only_control": {"visual_only": True, "disposition": "PASS"},
        "voice_backend_mismatch": {"voice_mismatch": True, "disposition": "PASS"},
        "persistence_or_recovery_diff": {"persistence_diff": True, "disposition": "PASS"},
        "secret_in_artifact": {"secret_in_artifact": True},
        "openclaw_undisposed_addition": {
            "upstream_project": "openclaw",
            "upstream_commit": "main",
            "qubit_path": None,
            "qubit_symbol": None,
            "production_caller": None,
            "disposition": "OPEN",
            "executed": False,
        },
        "unexecuted_open_row": {"disposition": "OPEN", "executed": False},
        "pass_without_reference": {"reference_executed": False, "disposition": "PASS"},
        "unpinned_reference_pass": {
            "disposition": "PASS",
            "unpinned_references": ["hermes"],
        },
    }
    row_keys = {"upstream_project", "upstream_commit", "qubit_path", "qubit_symbol"}
    for gate, mutation in triggers.items():
        trow, tres = _baseline_gate_inputs()
        for key, value in mutation.items():
            if key in row_keys:
                trow[key] = value
            tres[key] = value
        gates = apply_fail_closed_gates([trow], [tres])
        match = next(g for g in gates if g["gate"] == gate)
        chk.check(
            f"gate_trigger_{gate}",
            (not match["ok"]) and trow["capability_id"] in match["rows"],
            f"ok={match['ok']} rows={match['rows']}",
        )

    # inventory_coverage needs an input row with no result at all.
    cov_row, _ = _baseline_gate_inputs()
    cov_gates = apply_fail_closed_gates([cov_row], [])
    cov_match = next(g for g in cov_gates if g["gate"] == "inventory_coverage")
    chk.check(
        "gate_trigger_inventory_coverage",
        (not cov_match["ok"]) and cov_row["capability_id"] in cov_match["rows"],
        f"ok={cov_match['ok']} rows={cov_match['rows']}",
    )

    print(f"\nselftest: {chk.checks - chk.failures}/{chk.checks} checks passed")
    return EXIT_OK if chk.failures == 0 else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
