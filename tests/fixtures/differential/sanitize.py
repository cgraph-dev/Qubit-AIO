#!/usr/bin/env python3
"""Sanitize captured differential artifacts BEFORE they are written as fixtures.

Stdlib only, deterministic, importable. Two hard rules:

1. **No secrets, ever.** Secret-shaped strings are redacted. Environment maps
   keep only the environment variable *name* (``$OPENROUTER_API_KEY``); a literal
   value is replaced by its name reference, never stored.
2. **Nondeterminism is collapsed** using exactly the nine documented shapes:
   timestamps, uuids, temp paths, pids, session tokens, generated hashes and
   product branding, plus secret redaction. This mirrors the runner's
   normalizer allowlist so a sanitized artifact is directly comparable.

The module never reads a secret value from the environment: it only ever emits
``NAME`` / ``$NAME`` / ``env:NAME`` references.

CLI::

    python tests/fixtures/differential/sanitize.py --selftest
    python tests/fixtures/differential/sanitize.py capture.json fixture.json
    python tests/fixtures/differential/sanitize.py --env-name OPENROUTER_API_KEY
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

# --------------------------------------------------------------------------- #
# Redaction shapes
# --------------------------------------------------------------------------- #
# Secret-shaped values are replaced before anything is written. This list is a
# superset of the runner's scanner so a sanitized fixture can never trip the
# `secret_in_artifact` gate.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"glpat-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|"
        r"auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*\S{8,}"
    ),
)

_ENV_REF_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_ENV_PREFIX_RE = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Shape detectors (aligned with scripts/differential_parity.py).
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?)?$"
)
_RFC2822_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\s+"
    r"\d{2}:\d{2}(:\d{2})?\s+(GMT|UTC|[+-]\d{4})$"
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TEMP_PREFIXES = (
    "/tmp/", "/var/tmp/", "/private/tmp/", "/var/folders/",
    "/private/var/folders/",
)
_TIME_KEYS = frozenset(
    {"timestamp", "time", "ts", "date", "datetime", "created_at", "updated_at",
     "started_at", "finished_at", "expires_at", "expiry", "epoch", "mtime"}
)
_PID_KEYS = frozenset(
    {"pid", "ppid", "process_id", "proc_id", "child_pid", "parent_pid"}
)
_TOKEN_KEYS = frozenset(
    {"session_token", "token", "access_token", "refresh_token", "auth_token",
     "bearer", "bearer_token", "session_secret"}
)
_HASH_KEYS = ("hash", "sha", "digest", "checksum", "etag", "md5", "signature")
_TOKEN_VAL_RE = re.compile(r"^(sess|tok|sess_|tok_)[A-Za-z0-9._\-]{8,}$")
_HASH_VAL_RE = re.compile(r"^(sha256:)?[0-9a-f]{32,64}$")
_BRAND_RE = re.compile(r"\b(hermes|openclaw|qubit)\b", re.IGNORECASE)

PLACEHOLDERS = {
    "secret": "<redacted>",
    "timestamp": "<timestamp>",
    "uuid": "<uuid>",
    "temp_path": "<temp_path>",
    "pid": "<pid>",
    "session_token": "<session_token>",
    "generated_hash": "<generated_hash>",
    "product_branding": "<product>",
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# --------------------------------------------------------------------------- #
# Text scrubbing
# --------------------------------------------------------------------------- #
# Inline (substring) forms for free text: timestamps, uuids, temp paths, tokens
# and branding. Bare pids are only collapsed under pid-shaped keys because a
# plain integer is ambiguous in prose.
_INLINE_ISO_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?)?\b"
)
_INLINE_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_INLINE_TEMP_RE = re.compile(
    r"(?:/tmp/|/var/tmp/|/private/tmp/|/var/folders/|/private/var/folders/)"
    r"[^\s\"'\]})]*"
)
_INLINE_TOKEN_RE = re.compile(r"\b(?:sess|tok)_[A-Za-z0-9._\-]{8,}\b")


def _redact_secrets(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub(PLACEHOLDERS["secret"], text)
    return text


def scrub_text(text: str) -> str:
    """Scrub a free-text blob (logs, help output, DOM text)."""
    out = _redact_secrets(text)
    out = _INLINE_TOKEN_RE.sub(PLACEHOLDERS["session_token"], out)
    out = _INLINE_UUID_RE.sub(PLACEHOLDERS["uuid"], out)
    out = _INLINE_ISO_RE.sub(PLACEHOLDERS["timestamp"], out)
    out = _INLINE_TEMP_RE.sub(PLACEHOLDERS["temp_path"], out)
    out = _BRAND_RE.sub(PLACEHOLDERS["product_branding"], out)
    return out


# --------------------------------------------------------------------------- #
# Structured scrubbing
# --------------------------------------------------------------------------- #
def _is_time_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in _TIME_KEYS


def _is_epoch(key: Any, value: Any) -> bool:
    return (
        _is_time_key(key)
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 1_000_000_000 <= abs(value) <= 10_000_000_000_000
    )


def _is_pid_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return low in _PID_KEYS or low.endswith("_pid")


def scrub(obj: Any, *, key: Any = None) -> Any:
    """Recursively scrub a decoded JSON value. Returns a new object."""
    if isinstance(obj, Mapping):
        if key == "env" and all(isinstance(k, str) for k in obj):
            return scrub_env(obj)
        return {k: scrub(v, key=k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v, key=key) for v in obj]

    # Scalars under shape-bearing keys.
    if isinstance(key, str) and key.lower() in _TOKEN_KEYS:
        if isinstance(obj, str) and len(obj) >= 8:
            return PLACEHOLDERS["session_token"]
    if _is_pid_key(key) and not isinstance(obj, bool):
        if isinstance(obj, int) or (isinstance(obj, str) and obj.isdigit()):
            return PLACEHOLDERS["pid"]
    if _is_time_key(key) and (
        (isinstance(obj, str) and (_ISO_RE.match(obj) or _RFC2822_RE.match(obj)))
        or _is_epoch(key, obj)
    ):
        return PLACEHOLDERS["timestamp"]
    if isinstance(obj, str):
        low_key = key.lower() if isinstance(key, str) else ""
        if obj.startswith("sha256:") or (
            _HASH_VAL_RE.match(obj) and any(h in low_key for h in _HASH_KEYS)
        ):
            return PLACEHOLDERS["generated_hash"]
        if _UUID_RE.match(obj):
            return PLACEHOLDERS["uuid"]
        return scrub_text(obj)
    return obj


# --------------------------------------------------------------------------- #
# Environment references (names only, never values)
# --------------------------------------------------------------------------- #
def env_ref(name: str) -> str:
    """Return a safe reference for an environment variable NAME."""
    name = str(name).strip()
    if not _ENV_NAME_RE.match(name):
        raise ValueError(f"not a valid environment variable name: {name!r}")
    return "$" + name


def env_name(ref_or_name: str) -> str:
    """Extract the NAME from ``$NAME`` / ``env:NAME`` / ``NAME`` (no value)."""
    text = str(ref_or_name).strip()
    m = _ENV_REF_RE.match(text) or _ENV_PREFIX_RE.match(text)
    if m:
        return m.group(1)
    if _ENV_NAME_RE.match(text):
        return text
    raise ValueError(f"not an environment reference: {ref_or_name!r}")


def scrub_env(env: Any) -> dict:
    """Keep env var NAMES only. Any literal value becomes its name reference."""
    out: dict[str, str] = {}
    if not isinstance(env, Mapping):
        return out
    for key, value in env.items():
        name = str(key)
        if not _ENV_NAME_RE.match(name):
            continue
        try:
            ref = env_name(value) if isinstance(value, str) else name
        except ValueError:
            ref = name
        out[name] = env_ref(ref)
    return out


# --------------------------------------------------------------------------- #
# Artifact I/O
# --------------------------------------------------------------------------- #
def contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(p.search(value) for p in SECRET_PATTERNS)
    if isinstance(value, Mapping):
        return any(contains_secret(k) or contains_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(v) for v in value)
    return False


def scrub_artifact(src: str | Path, dst: str | Path | None = None) -> bytes:
    """Scrub a captured JSON or text artifact and (optionally) write it out.

    Fails closed: if a secret shape survives scrubbing, the output is rejected
    rather than written.
    """
    src_path = Path(src)
    raw = src_path.read_text(encoding="utf-8")
    try:
        decoded: Any = json.loads(raw)
    except ValueError:
        out_text = scrub_text(raw)
    else:
        decoded = scrub(decoded)
        out_text = json.dumps(decoded, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    if contains_secret(out_text):
        raise ValueError("secret shaped value survived scrubbing; refusing to write")
    data = out_text.encode("utf-8")
    if dst is not None:
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(data)
    return data


# --------------------------------------------------------------------------- #
# Selftest / CLI
# --------------------------------------------------------------------------- #
_SAMPLE = {
    "timestamp": "2026-09-16T03:26:11Z",
    "uuid": "8f14e45f-ceea-467a-9c1b-3a1f2b4c5d6e",
    "pid": 4242,
    "path": "/tmp/qubit-differential/evidence/run.json",
    "session_token": "sess_0123456789abcdef",
    "hash": "a" * 64,
    "banner": "Hermes/OpenClaw/Qubit bridge",
    "opaque": "sk-ant-abcdefghijklmnopqrstuvwx",
    "env": {"OPENROUTER_API_KEY": "literal-secret-value-1234"},
}


def selftest() -> int:
    scrubbed = scrub(json.loads(json.dumps(_SAMPLE)))
    checks = {
        scrubbed["timestamp"] == PLACEHOLDERS["timestamp"],
        scrubbed["uuid"] == PLACEHOLDERS["uuid"],
        scrubbed["pid"] == PLACEHOLDERS["pid"],
        scrubbed["path"] == PLACEHOLDERS["temp_path"],
        scrubbed["session_token"] == PLACEHOLDERS["session_token"],
        scrubbed["hash"] == PLACEHOLDERS["generated_hash"],
        PLACEHOLDERS["product_branding"] in scrubbed["banner"],
        scrubbed["opaque"] == PLACEHOLDERS["secret"],
        scrubbed["env"]["OPENROUTER_API_KEY"] == "$OPENROUTER_API_KEY",
        not contains_secret(scrubbed),
        scrub_env(_SAMPLE["env"]) == {"OPENROUTER_API_KEY": "$OPENROUTER_API_KEY"},
    }
    for ok in checks:
        if not ok:
            print("selftest FAILED", file=sys.stderr)
            return 1
    print("selftest OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sanitize",
        description="Scrub captured artifacts before they are stored as fixtures.",
    )
    parser.add_argument("src", nargs="?", help="captured JSON/text artifact")
    parser.add_argument("dst", nargs="?", help="scrubbed output path")
    parser.add_argument("--selftest", action="store_true", help="run built-in checks")
    parser.add_argument("--env-name", action="append", default=[],
                        help="emit a NAME reference for an env var (no value read)")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.env_name:
        for name in args.env_name:
            print(env_ref(name))
        if not args.src:
            return 0
    if not args.src:
        parser.error("src is required unless --selftest/--env-name is used")
    try:
        scrub_artifact(args.src, args.dst)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
