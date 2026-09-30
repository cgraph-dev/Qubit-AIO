"""Canonical Qubit environment reference registry (PRB-02).

ONE store behind every environment/config-reference surface. The dashboard
``GET/PUT/DELETE /api/env`` handlers, the CLI ``qubit auth status`` projection,
and the voice ``auth_status`` intent all read and write this single JSON store
(``<state_dir>/qubit_env_refs.json``). A value must be a credential *reference*
(``env:``/``vault:``/``file:``, with ``ref:`` retained as the vault alias); a raw
secret is never accepted, so the store never holds secret material itself.

The accepted kinds mirror the operator model the channel/credential surfaces
already use (``env:VAR`` -> process/runtime ``.env`` variable, ``file:PATH`` ->
a file outside git, ``vault:NAME`` -> an external secret-manager entry). Only
reference strings are ever stored or returned; a value is resolved server-side
by the consumer (see :func:`voicebridge.providers.credentials.resolve_key`),
never read into this module.

This module deliberately has no dependency on :mod:`voicebridge.qubit_compat`
so :mod:`voicebridge.auth_status` can read it without an import cycle.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

STORE_NAME = "qubit_env_refs.json"
#: pre-rename name; read once when only it exists, never written again.
LEGACY_STORE_NAME = "hermes_env_refs.json"
STORE_SCHEMA = "qubit-env-refs/1"
#: accepted reference prefixes (the only values the store will hold). ``ref:``
#: is retained as the legacy alias of ``vault:`` so pre-existing entries keep
#: resolving after the operator model widened to ``env:``/``file:``.
REF_RE = re.compile(r"^(env|vault|ref|file):")
REF_KINDS: Tuple[str, ...] = ("env", "vault", "ref", "file")
#: reference kinds the MCP/runtime client can actually resolve to a value.
RESOLVABLE_REF_KINDS: Tuple[str, ...] = ("env", "file")
MAX_REF_CHARS = 512
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _base(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir())
    except Exception:  # noqa: BLE001 - a config-less caller gets a safe temp
        return Path("/tmp")


def store_path(cfg: Any) -> Path:
    """The canonical env-reference store path (the write target) for *cfg*."""
    return _base(cfg) / STORE_NAME


def read_path(cfg: Any) -> Path:
    """The read source: canonical when present, else the legacy-named store.

    Non-destructive migration: when only the pre-rename file exists it is read
    once so no reference is lost, and the next save writes the canonical name.
    """
    new = store_path(cfg)
    if new.exists():
        return new
    legacy = _base(cfg) / LEGACY_STORE_NAME
    return legacy if legacy.exists() else new


def load(cfg: Any) -> Dict[str, str]:
    """Read the registry (values are reference strings only)."""
    try:
        raw = json.loads(read_path(cfg).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def names(cfg: Any) -> List[str]:
    """Sorted reference names (never values)."""
    return sorted(load(cfg).keys())


def runtime_env_names(cfg: Any) -> set:
    """Names present in the canonical runtime ``.env`` (never the values).

    The Keys page's ``is_set`` must reflect whether a key is actually present
    in Qubit's own runtime environment, not only in the ``vault:``/``ref:``
    registry. This reads the canonical ``<state_dir>/runtime/.env`` once and
    returns the dotenv-style variable NAMES only; values are never read out,
    cached, or returned. Any read error yields the empty set (fail closed).
    """
    try:
        from . import config as _config
        path = Path(_config.canonical_runtime_env_path())
    except Exception:  # noqa: BLE001 - unresolvable home -> no names
        return set()
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    found: set = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, _value = line.partition("=")
        key = key.strip()
        if sep and key:
            found.add(key)
    return found


def present_names(cfg: Any) -> set:
    """Union of reference names and runtime-``.env`` names (names only)."""
    return set(load(cfg).keys()) | runtime_env_names(cfg)


def is_present(cfg: Any, name: str) -> bool:
    """True when *name* is a registry reference or a runtime ``.env`` key."""
    return str(name or "").strip() in present_names(cfg)


def reference_present(cfg: Any, ref: str) -> bool:
    """True when *ref*'s target actually resolves (not merely registered).

    ``is_set`` must reflect whether the credential the reference names can
    actually be resolved, never only that a row exists in the registry. A
    resolvable kind is checked against the ONE canonical resolver
    (:func:`voicebridge.providers.credentials.resolve_key`): ``env:NAME`` is
    present only when ``NAME`` is in the process environment or the runtime
    ``.env``, and ``file:PATH`` only when the file exists and is non-empty.
    ``vault:``/``ref:`` name an external secret-manager entry for which Qubit
    has no resolver, so the declared registration is the only presence signal
    available and they remain present.

    The resolved value is never returned, logged, or cached here; only the
    boolean presence leaves this function. An unreadable/malformed reference
    fails closed to ``False``.
    """
    text = str(ref or "").strip()
    kind = reference_kind(text)
    if not kind:
        return False
    if kind in RESOLVABLE_REF_KINDS:
        try:
            from .providers.credentials import resolve_key

            _value, code = resolve_key("env-reference", text)
            return code == ""
        except Exception:  # noqa: BLE001 - presence check must fail closed
            return False
    return True


def is_resolved(ref: str) -> bool:
    """True when a credential reference resolves to a non-empty secret.

    Delegates to the shared credential authority
    (:func:`voicebridge.providers.credentials.resolve_key`): ``env:``/``file:``
    resolve locally, ``vault:``/``ref:`` have no Qubit-side resolver and are
    honestly not-set. Only the boolean is returned — the resolved value is
    never read out, cached, logged, or returned. Fail-closed on any error.
    """
    try:
        from .providers.credentials import resolve_key

        value, code = resolve_key("env-ref", str(ref or ""))
        return code == "" and bool(value)
    except Exception:  # noqa: BLE001 - resolution must fail closed
        return False


def resolved_names(cfg: Any) -> set:
    """Names whose credential actually resolves (names only, never values).

    The union of the process environment, the canonical runtime ``.env``, and
    registry references that truly resolve. This is the honest replacement for
    conflating "a registry row exists" with "the credential is present".
    """
    out = set(runtime_env_names(cfg))
    out |= set(os.environ.keys())
    for name, ref in load(cfg).items():
        if is_resolved(ref):
            out.add(str(name))
    return out


def _save(cfg: Any, data: Dict[str, str]) -> None:
    path = store_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    if path.exists():
        try:
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
        except OSError:
            pass
    os.replace(tmp, path)


def reference_kind(value: str) -> str:
    """The reference kind of *value* (``env``/``vault``/``ref``/``file``), or "".

    A value with no recognized prefix is a raw secret (or malformed) and is
    never accepted by the store.
    """
    text = str(value or "").strip()
    if ":" not in text:
        return ""
    kind = text.split(":", 1)[0].lower()
    return kind if kind in REF_KINDS else ""


def validate_reference(value: str) -> Tuple[bool, str]:
    """Validate a credential reference; returns ``(ok, detail)``.

    Accepts ``env:NAME``, ``vault:NAME``, ``file:PATH`` and the legacy
    ``ref:NAME`` alias. The shape of each kind is checked (env var name, file
    path) and file references are refused when they point at browser or agent
    private credential stores, mirroring :mod:`voicebridge.providers.credentials`.
    A raw secret value never validates.
    """
    text = str(value or "").strip()
    if not text:
        return False, "an env reference is required"
    if len(text) > MAX_REF_CHARS:
        return False, "reference too long"
    if any(ch in text for ch in "\n\r\t\x00"):
        return False, "reference must not contain control characters"
    kind = reference_kind(text)
    if not kind:
        return False, (
            "env writes accept env:/vault:/file: references only, never secret "
            "values; store the secret in the OS keyring/manager")
    body = text.split(":", 1)[1].strip()
    if not body:
        return False, f"{kind}: reference is missing a name/path"
    if kind == "env":
        if not _ENV_VAR_RE.match(body):
            return False, (
                "env: reference must name a variable matching "
                "^[A-Za-z_][A-Za-z0-9_]*$")
    elif kind == "file":
        low = body.replace("\\", "/")
        if not (low.startswith("/") or low.startswith("~")):
            return False, "file: reference must be an absolute path or ~/ path"
        if "/../" in low or low.endswith("/..") or low.startswith("../"):
            return False, "file: reference must not traverse parent directories"
        try:
            from .providers.credentials import (
                looks_like_browser_secret,
                looks_like_opencode_private_db,
            )
            if looks_like_browser_secret(low) or \
                    looks_like_opencode_private_db(low):
                return False, (
                    "file: reference must not point at browser or agent "
                    "private credential stores")
        except Exception:  # noqa: BLE001 - validator stays fail-closed on shape
            pass
    return True, ""


def is_reference(value: str) -> bool:
    """True when *value* is a valid credential reference, never a raw secret."""
    return validate_reference(value)[0]


def valid_name(name: str) -> bool:
    """True when *name* is an acceptable reference name (env-var shaped)."""
    return bool(_ENV_VAR_RE.match(str(name or "").strip()))


def is_resolvable_reference(value: str) -> bool:
    """True when *value* is a reference the runtime client can resolve.

    ``env:``/``file:`` resolve locally; ``vault:``/``ref:`` name an external
    secret manager entry with no Qubit-side resolver, so a consumer that must
    pass a concrete value to a child (the MCP client) cannot use them.
    """
    ok, _ = validate_reference(value)
    return ok and reference_kind(value) in RESOLVABLE_REF_KINDS


def set_ref(cfg: Any, name: str, ref: str) -> Dict[str, Any]:
    """Persist one reference. Raises ``ValueError`` for a bad name or secret."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("env reference name required")
    if not _ENV_VAR_RE.match(clean):
        raise ValueError(
            "env reference name must match ^[A-Za-z_][A-Za-z0-9_]*$")
    value = str(ref or "").strip()
    ok, detail = validate_reference(value)
    if not ok:
        raise ValueError(detail)
    data = load(cfg)
    data[clean] = value
    _save(cfg, data)
    return {"name": clean, "count": len(data)}


def remove_ref(cfg: Any, name: str) -> Dict[str, Any]:
    """Remove one reference; returns whether it existed."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("env reference name required")
    data = load(cfg)
    existed = clean in data
    data.pop(clean, None)
    if existed:
        _save(cfg, data)
    return {"name": clean, "removed": existed, "count": len(data)}
