"""Filesystem secret vault for the Business OS.

The vault is deliberately boring: one ``0600`` value file and one ``0600``
metadata file per secret inside a ``0700`` directory tree supplied by the
caller. No secret is ever returned by a list or status call; callers get a
stable reference plus a SHA-256 fingerprint. A tool can only resolve a secret
that is explicitly bound to it, still exists, and has not expired or been
revoked.

There is one more job this module owns: *canary scanning*. Every live value in
the vault (including a dedicated canary secret) is redacted out of a text or
structured payload before that payload can reach a prompt, log, memory entry,
workspace file or artifact. Redaction is exact-value based, so it catches a
credential the generic regexes in :mod:`voicebridge.business.util` cannot know
about.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets as _secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote, quote_plus

from .errors import BAD_INPUT_CODE, BusinessError
from .ids import new_id
from .util import json_dumps, json_loads, now_iso

SECRET_NOT_FOUND_CODE = "BUSINESS_SECRET_NOT_FOUND"
SECRET_NOT_BOUND_CODE = "BUSINESS_SECRET_NOT_BOUND"
SECRET_EXPIRED_CODE = "BUSINESS_SECRET_EXPIRED"
SECRET_REVOKED_CODE = "BUSINESS_SECRET_REVOKED"
SECRET_BAD_VALUE_CODE = "BUSINESS_SECRET_BAD_VALUE"

REDACTED = "***REDACTED***"
_CANARY_PREFIX = "QUBIT-CANARY-"
_MAX_VALUE_BYTES = 64 * 1024
_MAX_NAME = 200
_REF_PREFIX = "sec"


def fingerprint_value(value: str) -> str:
    """Return the stable, non-reversible fingerprint of a secret value."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refuse(code: str, detail: str) -> "BusinessError":
    return BusinessError(code, detail)


def _utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return now_iso(ts)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` atomically with restrictive permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _read_value(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as handle:
            return handle.read().decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - corrupt local state
        raise _refuse(SECRET_NOT_FOUND_CODE, "secret value is unreadable") from exc


def _validate_value(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _refuse(SECRET_BAD_VALUE_CODE, "secret value must be a non-empty string")
    if "\x00" in value:
        raise _refuse(SECRET_BAD_VALUE_CODE, "secret value must not contain NUL")
    if len(value.encode("utf-8")) > _MAX_VALUE_BYTES:
        raise _refuse(
            SECRET_BAD_VALUE_CODE,
            f"secret value exceeds {_MAX_VALUE_BYTES} bytes",
        )
    return value


def _normalise_tools(tools: Iterable[str] | None) -> tuple[str, ...]:
    if not tools:
        return ()
    if isinstance(tools, str):
        tools = (tools,)
    cleaned: list[str] = []
    for tool in tools:
        if not isinstance(tool, str) or not tool.strip():
            raise _refuse(BAD_INPUT_CODE, "tool bindings must be non-empty names")
        name = tool.strip()
        if name not in cleaned:
            cleaned.append(name)
    return tuple(cleaned)


class SecretVault:
    """A tenant-scoped, filesystem-backed secret store.

    ``root`` is caller supplied so tests and callers can point the vault at a
    throwaway directory. All directories are created ``0700`` and every file
    is written ``0600`` through an atomic ``os.replace``.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._secrets_dir = self.root / "secrets"
        self._lock_path = self.root / ".vault.lock"
        self._secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self._secrets_dir, 0o700)
        self._thread_lock = threading.RLock()

    # -- paths ---------------------------------------------------------
    def _meta_path(self, ref: str) -> Path:
        self._check_ref(ref)
        return self._secrets_dir / f"{ref}.json"

    def _value_path(self, ref: str) -> Path:
        self._check_ref(ref)
        return self._secrets_dir / f"{ref}.value"

    @staticmethod
    def _check_ref(ref: Any) -> None:
        if (
            not isinstance(ref, str)
            or not ref.startswith(_REF_PREFIX + "_")
            or "/" in ref
            or "\\" in ref
            or ".." in ref
        ):
            raise _refuse(SECRET_NOT_FOUND_CODE, "secret reference is not valid")

    # -- metadata ------------------------------------------------------
    def _load(self, ref: str, tenant_id: str | None = None) -> dict:
        path = self._meta_path(ref)
        try:
            raw = _read_value(path)
        except (FileNotFoundError, OSError):
            raise _refuse(SECRET_NOT_FOUND_CODE, "secret reference does not exist")
        meta = json_loads(raw, None)
        if not isinstance(meta, dict):
            raise _refuse(SECRET_NOT_FOUND_CODE, "secret metadata is unreadable")
        if tenant_id is not None and meta.get("tenant_id") != tenant_id:
            # A secret from another tenant is indistinguishable from a missing one.
            raise _refuse(SECRET_NOT_FOUND_CODE, "secret reference does not exist")
        return meta

    def _store(self, meta: Mapping[str, Any], value: str | None = None) -> dict:
        ref = str(meta["ref"])
        if value is not None:
            _atomic_write(self._value_path(ref), value.encode("utf-8"))
        _atomic_write(
            self._meta_path(ref),
            json_dumps(dict(meta)).encode("utf-8"),
        )
        return dict(meta)

    def _public(self, meta: Mapping[str, Any]) -> dict:
        return {
            "ref": meta["ref"],
            "tenant_id": meta.get("tenant_id"),
            "owner_id": meta.get("owner_id"),
            "name": meta.get("name"),
            "kind": meta.get("kind", "generic"),
            "fingerprint": meta.get("fingerprint"),
            "version": meta.get("version", 1),
            "tools": list(meta.get("tools") or ()),
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "expires_at": meta.get("expires_at"),
            "revoked": bool(meta.get("revoked", False)),
        }

    # -- writes --------------------------------------------------------
    def put_secret(
        self,
        *,
        tenant_id: str,
        owner_id: str | None,
        name: str,
        value: str,
        tools: Iterable[str] | None = None,
        kind: str = "generic",
        ttl_seconds: float | None = None,
        expires_at: float | None = None,
        now: float | None = None,
    ) -> dict:
        """Store a new secret and return its public metadata (never the value)."""
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise _refuse(BAD_INPUT_CODE, "tenant_id is required")
        if not isinstance(name, str) or not name.strip() or len(name) > _MAX_NAME:
            raise _refuse(BAD_INPUT_CODE, "secret name must be a short non-empty string")
        clean_value = _validate_value(value)
        bindings = _normalise_tools(tools)
        moment = float(now if now is not None else _now())
        expiry = expires_at
        if expiry is None and ttl_seconds is not None:
            expiry = moment + float(ttl_seconds)
        if expiry is not None and expiry <= moment:
            raise _refuse(BAD_INPUT_CODE, "secret expiry must be in the future")
        ref = new_id(_REF_PREFIX)
        meta = {
            "ref": ref,
            "tenant_id": tenant_id.strip(),
            "owner_id": owner_id,
            "name": name.strip(),
            "kind": kind,
            "fingerprint": fingerprint_value(clean_value),
            "version": 1,
            "tools": list(bindings),
            "created_at": _utc(moment),
            "updated_at": _utc(moment),
            "expires_at": _utc(expiry),
            "expires_epoch": expiry,
            "revoked": False,
        }
        with self._thread_lock:
            self._store(meta, clean_value)
        return self._public(meta)

    def rotate(
        self,
        secret_ref: str,
        new_value: str,
        *,
        tenant_id: str | None = None,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> dict:
        """Replace the value under the same reference, bumping its version."""
        clean_value = _validate_value(new_value)
        moment = float(now if now is not None else _now())
        with self._thread_lock:
            meta = self._load(secret_ref, tenant_id)
            if meta.get("revoked"):
                raise _refuse(SECRET_REVOKED_CODE, "a revoked secret cannot be rotated")
            meta["fingerprint"] = fingerprint_value(clean_value)
            meta["version"] = int(meta.get("version", 1)) + 1
            meta["updated_at"] = _utc(moment)
            if ttl_seconds is not None:
                meta["expires_epoch"] = moment + float(ttl_seconds)
                meta["expires_at"] = _utc(meta["expires_epoch"])
            self._store(meta, clean_value)
            return self._public(meta)

    def revoke(self, secret_ref: str, *, tenant_id: str | None = None) -> dict:
        """Mark a secret revoked. Existing value bytes are removed."""
        with self._thread_lock:
            meta = self._load(secret_ref, tenant_id)
            meta["revoked"] = True
            meta["updated_at"] = _utc(_now())
            self._store(meta)
            try:
                self._value_path(secret_ref).unlink()
            except FileNotFoundError:
                pass
            return self._public(meta)

    # -- reads ---------------------------------------------------------
    def get_secret_ref(self, secret_ref: str, *, tenant_id: str | None = None) -> dict:
        """Return public metadata (ref + fingerprint, never the value)."""
        return self._public(self._load(secret_ref, tenant_id))

    def list_secrets(self, *, tenant_id: str | None = None) -> list[dict]:
        """List public metadata. Values are never read or returned."""
        rows: list[dict] = []
        for path in sorted(self._secrets_dir.glob("*.json")):
            try:
                meta = json_loads(_read_value(path), None)
            except (FileNotFoundError, OSError):
                continue
            if not isinstance(meta, dict):
                continue
            if tenant_id is not None and meta.get("tenant_id") != tenant_id:
                continue
            rows.append(self._public(meta))
        return rows

    def status(self, secret_ref: str, *, tenant_id: str | None = None,
               now: float | None = None) -> dict:
        """Return public metadata plus a derived, value-free lifecycle state."""
        meta = self._load(secret_ref, tenant_id)
        moment = float(now if now is not None else _now())
        state = "active"
        if meta.get("revoked"):
            state = "revoked"
        elif _expired(meta, moment):
            state = "expired"
        row = self._public(meta)
        row["state"] = state
        return row

    def resolve_for_tool(
        self,
        secret_ref: str,
        tool: str,
        *,
        tenant_id: str | None = None,
        now: float | None = None,
    ) -> str:
        """Resolve a secret value for the one tool bound to it.

        Refuses a wrong/absent tool, a revoked secret or an expired secret.
        The returned value is the only place a secret leaves the vault.
        """
        if not isinstance(tool, str) or not tool.strip():
            raise _refuse(BAD_INPUT_CODE, "tool name is required")
        tool_name = tool.strip()
        moment = float(now if now is not None else _now())
        with self._thread_lock:
            meta = self._load(secret_ref, tenant_id)
            if meta.get("revoked"):
                raise _refuse(SECRET_REVOKED_CODE, "secret has been revoked")
            if _expired(meta, moment):
                raise _refuse(SECRET_EXPIRED_CODE, "secret has expired")
            if tool_name not in (meta.get("tools") or ()):
                raise _refuse(
                    SECRET_NOT_BOUND_CODE,
                    f"secret is not bound to tool {tool_name!r}",
                )
            try:
                return _read_value(self._value_path(secret_ref))
            except FileNotFoundError:
                raise _refuse(SECRET_NOT_FOUND_CODE, "secret value file is missing")

    # -- canary + scanning ---------------------------------------------
    def canary(
        self,
        *,
        tenant_id: str,
        owner_id: str | None = None,
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> dict:
        """Create a unique canary secret that must never appear in payloads.

        Tests place the canary value in a payload, run :meth:`scan_text` and
        assert only :data:`REDACTED` remains.
        """
        token = _CANARY_PREFIX + _secrets.token_hex(16)
        return self.put_secret(
            tenant_id=tenant_id,
            owner_id=owner_id,
            name="canary",
            value=token,
            tools=(),
            kind="canary",
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def _live_values(self, now: float | None = None) -> list[str]:
        # Expiry stops resolution but does not make stored bytes harmless:
        # every non-revoked value is still scanned and redacted.
        values: list[str] = []
        for path in sorted(self._secrets_dir.glob("*.json")):
            ref = path.stem
            try:
                meta = json_loads(_read_value(path), None)
                if not isinstance(meta, dict):
                    continue
                if meta.get("revoked"):
                    continue
                values.append(_read_value(self._value_path(ref)))
            except (FileNotFoundError, OSError):
                continue
        return values

    def scan_text(self, text: Any, *, now: float | None = None) -> str:
        """Return ``text`` with every live secret value redacted."""
        if not isinstance(text, str):
            return text
        out = text
        for value in self._live_values(now=now):
            if not value:
                continue
            for variant in _variants(value):
                if variant and variant in out:
                    out = out.replace(variant, REDACTED)
        return out

    def scan_payload(self, payload: Any, *, now: float | None = None) -> Any:
        """Recursively redact live secret values from a structured payload."""
        if isinstance(payload, str):
            return self.scan_text(payload, now=now)
        if isinstance(payload, dict):
            return {
                (self.scan_text(key, now=now) if isinstance(key, str) else key):
                    self.scan_payload(value, now=now)
                for key, value in payload.items()
            }
        if isinstance(payload, (list, tuple)):
            return [self.scan_payload(value, now=now) for value in payload]
        return payload

    def contains_secret(self, text: Any, *, now: float | None = None) -> bool:
        """Return True when any live secret value appears in ``text``."""
        if not isinstance(text, str):
            return False
        return any(
            variant and variant in text
            for value in self._live_values(now=now)
            for variant in _variants(value)
        )


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _expired(meta: Mapping[str, Any], moment: float) -> bool:
    expiry = meta.get("expires_epoch")
    if expiry is None:
        return False
    try:
        return float(expiry) <= moment
    except (TypeError, ValueError):
        return True


def _variants(value: str) -> list[str]:
    """Encoding variants a secret can take when it leaks into a payload."""
    variants = {value}
    variants.add(quote(value, safe=""))
    variants.add(quote_plus(value))
    try:
        raw = base64.b64encode(value.encode("utf-8")).decode("ascii")
        variants.add(raw)
        variants.add(raw.rstrip("="))
    except (ValueError, UnicodeError):  # pragma: no cover - defensive
        pass
    return [item for item in variants if item]
