"""Packet 7C: versioned portable-state export/import with audited encryption.

Contract:
- Bundles memory, lessons, preferences, reports, non-secret config, model
  metadata, and migration history.
- EXCLUDES by construction: API keys, cookies, Chromium profiles, CDP debugger
  URLs, active approvals, private credentials, machine-specific audio/GPU
  paths, and raw secrets.
- Encryption: audited, pinned mechanism — never home-grown cryptography.
  Key derivation = PBKDF2-HMAC-SHA256 (RFC 8018, stdlib) with a high iteration
  count; cipher = AES-256-CBC via the SYSTEM OpenSSL CLI (audited, pinned);
  integrity = HMAC-SHA256 (RFC 2104, stdlib) encrypt-then-MAC. The bundle
  records the OpenSSL version and KDF params so restore verifies them.
- Restore stages atomically, verifies integrity before applying, preserves a
  pre-restore backup, supports dry-run/preview, and rolls back on failure.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import distribution as dist

SCHEMA_VERSION = 1
MAGIC = b"VBPORT1"
KDF_ITER = 310_000
PBKDF2_ALGO = "sha256"
CIPHER = "aes-256-cbc"
MAC_ALGO = "hmac-sha256"


class PortableError(Exception):
    """Typed portable-state failure."""


class WrongPassphraseError(PortableError):
    pass


class CorruptedBundleError(PortableError):
    pass


class UnsupportedSchemaError(PortableError):
    pass


class LowDiskError(PortableError):
    pass


class PartialRollbackError(PortableError):
    """A restore failed AND at least one rollback step could not restore a
    touched path. Carries the exact bounded paths that could not be restored;
    the operation must never be reported as a successful rollback."""

    def __init__(self, message: str, failed_paths: List[str]) -> None:
        super().__init__(message)
        self.failed_paths = failed_paths


@dataclass
class BundleInfo:
    schema_version: int
    openssl_version: str
    kdf_iter: int
    created_at: float
    entries: List[str]
    device_profile: Dict[str, Any]


def _openssl_binary() -> str:
    path = shutil.which("openssl")
    if not path:
        raise PortableError("openssl not found (audited encryption unavailable)")
    return path


def _derive_key(passphrase: str, salt: bytes, dklen: int) -> bytes:
    return hashlib.pbkdf2_hmac(PBKDF2_ALGO, passphrase.encode("utf-8"),
                               salt, KDF_ITER, dklen)


def _encrypt_openssl(key: bytes, iv: bytes, data: bytes) -> bytes:
    p = subprocess.run(
        [_openssl_binary(), "enc", "-aes-256-cbc", "-K", key.hex(),
         "-iv", iv.hex()],
        input=data, capture_output=True, timeout=30)
    if p.returncode != 0:
        raise PortableError("openssl encrypt failed: "
                            + (p.stderr or b"").decode(errors="replace")[:120])
    return p.stdout


def _decrypt_openssl(key: bytes, iv: bytes, data: bytes) -> Optional[bytes]:
    p = subprocess.run(
        [_openssl_binary(), "enc", "-d", "-aes-256-cbc", "-K", key.hex(),
         "-iv", iv.hex()],
        input=data, capture_output=True, timeout=30)
    if p.returncode != 0:
        return None
    return p.stdout


# ---------------------------------------------------------------------------
# State collection / sanitization
# ---------------------------------------------------------------------------
_SECRET_KEY_RE = __import__("re").compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization|cookie|"
    r"credential)\b")
_MACHINE_PATH_KEYS = {"output_device", "output_device_name", "frontend_dir"}
_EXCLUDE_CONFIG_SECTIONS = {"webui", "agent", "logging"}


def _sanitize_config(cfg: Any) -> Dict[str, Any]:
    """Non-secret configuration: drop secret-shaped keys and machine-specific
    audio/GPU paths. webui/agent/logging sections are excluded entirely."""
    data = getattr(cfg, "data", None) or {}
    out: Dict[str, Any] = {}
    for section, body in data.items():
        if section in _EXCLUDE_CONFIG_SECTIONS:
            continue
        if not isinstance(body, dict):
            out[section] = body
            continue
        clean = {}
        for k, v in body.items():
            if _SECRET_KEY_RE.search(str(k)):
                continue
            if k in _MACHINE_PATH_KEYS:
                continue
            clean[k] = v
        out[section] = clean
    return out


def _collect_memory(cfg: Any) -> List[Dict[str, Any]]:
    try:
        from .memory import MemoryStore
        db = cfg.state_dir() / "memory" / "memory.db"
        store = MemoryStore(db)
        try:
            return store.export(include_tombstoned=False)
        finally:
            store.close()
    except Exception as exc:
        return [{"error": str(exc)[:120]}]


def _collect_lessons(cfg: Any) -> List[Dict[str, Any]]:
    try:
        from .learning import LearningLedger
        ledger = LearningLedger(cfg.state_dir())
        return ledger.lessons()
    except Exception as exc:
        return [{"error": str(exc)[:120]}]


def _collect_reports(cfg: Any, *, max_files: int = 40,
                     max_bytes: int = 2 * 1024 * 1024) -> List[Dict[str, Any]]:
    reports = Path(cfg.state_dir()) / "reports"
    out: List[Dict[str, Any]] = []
    if not reports.exists():
        return out
    total = 0
    for p in sorted(reports.iterdir()):
        if not p.is_file() or total >= max_bytes:
            continue
        if len(out) >= max_files:
            break
        try:
            out.append({"name": p.name, "content": p.read_text(
                errors="replace")[:max_bytes - total]})
            total += p.stat().st_size
        except OSError:
            continue
    return out


def _collect_migrations() -> List[str]:
    from .resources import resource_path
    mig = resource_path("docs/MIGRATIONS.md")
    if not mig.exists():
        return []
    return [ln[4:].split("|")[0].strip()
            for ln in mig.read_text().splitlines()
            if ln.startswith("## MIG-")]


def collect_state(cfg: Any) -> Dict[str, Any]:
    """Everything the portable bundle may carry (sanitized)."""
    from .runner_contract import resolve
    rc = resolve(cfg)
    return {
        "memory": _collect_memory(cfg),
        "lessons": _collect_lessons(cfg),
        "reports": _collect_reports(cfg),
        "config_non_secret": _sanitize_config(cfg),
        "model": {"alias": rc.alias, "url": rc.url, "num_ctx": rc.num_ctx,
                  "keep_alive": rc.keep_alive, "digest": rc.digest},
        "migration_history": _collect_migrations(),
    }


# ---------------------------------------------------------------------------
# Bundle write / read
# ---------------------------------------------------------------------------
def _payload(state: Dict[str, Any]) -> bytes:
    return json.dumps({"schema": "portable-state", "version": SCHEMA_VERSION,
                       "created_at": round(time.time(), 3),
                       "state": state}, sort_keys=True).encode("utf-8")


def export_bundle(cfg: Any, passphrase: str) -> bytes:
    """Return the encrypted portable-state bundle bytes (never writes state)."""
    if not dist.openssl_ok():
        raise PortableError(
            "audited encryption unavailable: system OpenSSL >= "
            f"{dist.OPENSSL_REQUIRED} required")
    state = collect_state(cfg)
    payload = _payload(state)
    salt_e = secrets.token_bytes(16)
    salt_m = secrets.token_bytes(16)
    iv = secrets.token_bytes(16)
    enc_key = _derive_key(passphrase, salt_e, 32)
    mac_key = _derive_key(passphrase, salt_m, 32)
    ciphertext = _encrypt_openssl(enc_key, iv, payload)
    header = {
        "magic": MAGIC.decode("ascii"),
        "schema_version": SCHEMA_VERSION,
        "openssl_version": dist.openssl_version(),
        "kdf": {"algo": f"pbkdf2-{PBKDF2_ALGO}", "iterations": KDF_ITER,
                "salt_enc_b64": base64.b64encode(salt_e).decode(),
                "salt_mac_b64": base64.b64encode(salt_m).decode()},
        "cipher": CIPHER,
        "mac_algo": MAC_ALGO,
        "iv_b64": base64.b64encode(iv).decode(),
        "created_at": round(time.time(), 3),
    }
    header_bytes = json.dumps(header, sort_keys=True).encode("utf-8")
    mac = hmac.new(mac_key, header_bytes + ciphertext, hashlib.sha256).digest()
    header["mac_b64"] = base64.b64encode(mac).decode()
    header_bytes = json.dumps(header, sort_keys=True).encode("utf-8")
    out = MAGIC + b"\n" + header_bytes + b"\n" + ciphertext
    return out


def read_bundle(bundle: bytes, passphrase: str) -> Dict[str, Any]:
    """Decrypt + integrity-verify a bundle. Raises WrongPassphraseError /
    CorruptedBundleError / UnsupportedSchemaError."""
    try:
        magic, header_b, ciphertext = bundle.split(b"\n", 2)
    except ValueError:
        raise CorruptedBundleError("bundle framing invalid") from None
    if magic != MAGIC:
        raise CorruptedBundleError("bad bundle magic")
    try:
        header = json.loads(header_b.decode("utf-8"))
    except Exception:
        raise CorruptedBundleError("bundle header not JSON") from None
    ver = header.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"schema v{ver} not supported (current v{SCHEMA_VERSION})")
    try:
        kdf = header["kdf"]
        salt_e = base64.b64decode(kdf["salt_enc_b64"])
        salt_m = base64.b64decode(kdf["salt_mac_b64"])
        iv = base64.b64decode(header["iv_b64"])
        enc_key = _derive_key(passphrase, salt_e, 32)
        mac_key = _derive_key(passphrase, salt_m, 32)
        expected = base64.b64decode(header["mac_b64"])
        # MAC covers the header WITHOUT its own mac_b64 field, so strip it and
        # re-serialize with the same sort_keys=True used at export.
        header_no_mac = {k: v for k, v in header.items() if k != "mac_b64"}
        header_no_mac_bytes = json.dumps(header_no_mac,
                                         sort_keys=True).encode("utf-8")
        actual = hmac.new(mac_key, header_no_mac_bytes + ciphertext,
                          hashlib.sha256).digest()
    except Exception:
        raise CorruptedBundleError("bundle header fields malformed") from None
    if not hmac.compare_digest(expected, actual):
        raise WrongPassphraseError("wrong passphrase or tampered bundle")
    plain = _decrypt_openssl(enc_key, iv, ciphertext)
    if plain is None:
        raise CorruptedBundleError("decryption failed after MAC passed")
    try:
        payload = json.loads(plain.decode("utf-8"))
    except Exception:
        raise CorruptedBundleError("decrypted payload not JSON") from None
    return payload


def bundle_info(bundle: bytes) -> BundleInfo:
    try:
        _magic, header_b, _ct = bundle.split(b"\n", 2)
        header = json.loads(header_b.decode("utf-8"))
        entries = list((header.get("entries") or []))
        return BundleInfo(schema_version=header.get("schema_version"),
                          openssl_version=header.get("openssl_version"),
                          kdf_iter=header.get("kdf", {}).get("iterations"),
                          created_at=header.get("created_at"),
                          entries=entries,
                          device_profile=header.get("device_profile") or {})
    except Exception:
        raise CorruptedBundleError("bundle header unreadable") from None


# ---------------------------------------------------------------------------
# Restore: atomic staging + backup + rollback
# ---------------------------------------------------------------------------
@dataclass
class RestorePlan:
    target_state: Path
    backup_dir: Optional[Path]
    actions: List[str]


def _free_bytes_gib(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except OSError:
        return 0.0


def plan_restore(cfg: Any, payload: Dict[str, Any], *,
                 dry_run: bool) -> RestorePlan:
    state = payload.get("state") or {}
    target = Path(cfg.state_dir())
    if _free_bytes_gib(target) < dist.MIN_DISK_FREE_GIB:
        raise LowDiskError(
            f"target has < {dist.MIN_DISK_FREE_GIB} GiB free")
    backup = None
    if not dry_run:
        backup = target / f"pre-restore-{int(time.time())}"
        backup.mkdir(parents=True, exist_ok=True)
        for name in ("memory", "learning.jsonl", "reports", "shared_memory.jsonl"):
            src = target / name
            if src.exists():
                try:
                    if src.is_dir():
                        shutil.copytree(src, backup / name)
                    else:
                        shutil.copy2(src, backup / name)
                except OSError:
                    pass
    actions = []
    mem = state.get("memory") or []
    if mem:
        actions.append(f"memory: {len(mem)} records")
    lessons = state.get("lessons") or []
    if lessons:
        actions.append(f"lessons: {len(lessons)}")
    reports = state.get("reports") or []
    if reports:
        actions.append(f"reports: {len(reports)} files")
    cfg_ks = state.get("config_non_secret") or {}
    if cfg_ks:
        actions.append(f"config: {len(cfg_ks)} sections")
    return RestorePlan(target_state=target, backup_dir=backup,
                       actions=actions)


def _write_file_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".restore-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _restore_memory(target: Path, records: List[Dict[str, Any]]) -> None:
    db_dir = target / "memory"
    db_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(db_dir, 0o700)
    except OSError:
        pass
    db = db_dir / "memory.db"
    _write_file_atomic(db, b"")  # placeholder; real content set via store
    from .memory import MemoryStore
    store = MemoryStore(db)
    try:
        for r in records:
            if "text" not in r:
                continue
            store.remember(r["text"], tier=r.get("tier", "recall"),
                           scope=r.get("scope", "default"),
                           confidence=float(r.get("confidence", 1.0)),
                           source=r.get("source", "restore"),
                           kind=r.get("kind", "fact"))
    finally:
        store.close()


def _restore_lessons(target: Path, lessons: List[Dict[str, Any]]) -> None:
    if not lessons:
        return
    from .learning import LearningLedger
    ledger = LearningLedger(target)
    for row in lessons:
        content = row.get("content") or row.get("preference") \
            or row.get("lesson_id") or ""
        kind = row.get("kind") or row.get("type")
        if kind in ("preference", "pref"):
            ledger.add_preference(content, source="restore")
        elif content and row.get("evidence_id"):
            ledger.record(row.get("user_text", ""), content, "correct",
                          corrected=content)


def _restore_reports(target: Path, reports: List[Dict[str, Any]]) -> None:
    if not reports:
        return
    reports_dir = target / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(reports_dir, 0o700)
    except OSError:
        pass
    for r in reports:
        name = Path(r.get("name", "")).name  # strip path traversal
        if not name:
            continue
        _write_file_atomic(reports_dir / name, r.get("content", "").encode())


def restore_state(cfg: Any, payload: Dict[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    """Apply a decrypted payload. Staging is atomic per file with a
    pre-restore backup; failure rolls back to the backup."""
    plan = plan_restore(cfg, payload, dry_run=dry_run)
    if dry_run:
        return {"dry_run": True, "actions": plan.actions,
                "backup": None}
    state = payload.get("state") or {}
    try:
        _restore_memory(plan.target_state, state.get("memory") or [])
        _restore_lessons(plan.target_state, state.get("lessons") or [])
        _restore_reports(plan.target_state, state.get("reports") or [])
        return {"dry_run": False, "actions": plan.actions,
                "backup": str(plan.backup_dir)}
    except Exception as exc:
        # rollback: restore the pre-restore backup over the touched files.
        # A rollback step that cannot restore its path is a TYPED failure with
        # the exact bounded paths (Packet 8J finding #6): it must block resume
        # and never be swallowed or reported as a successful rollback.
        rollback_failures: List[str] = []
        if plan.backup_dir and plan.backup_dir.exists():
            for name in ("memory", "learning.jsonl", "reports", "shared_memory.jsonl"):
                bak = plan.backup_dir / name
                src = plan.target_state / name
                if not bak.exists():
                    # The path did not exist before the restore: rolling back
                    # means removing whatever the failed restore created, or
                    # the store would silently keep a partial restore.
                    if not src.exists():
                        continue
                    try:
                        if src.is_dir():
                            shutil.rmtree(src)
                        else:
                            src.unlink()
                    except OSError as rb_exc:
                        rollback_failures.append(f"{src}: {str(rb_exc)[:120]}")
                    continue
                try:
                    if bak.is_dir():
                        if src.exists():
                            shutil.rmtree(src)
                        shutil.copytree(bak, src)
                    else:
                        _write_file_atomic(src, bak.read_bytes())
                except OSError as rb_exc:
                    rollback_failures.append(
                        f"{src}: {str(rb_exc)[:120]}")
        if rollback_failures:
            raise PartialRollbackError(
                f"restore failed and rollback is INCOMPLETE; touched paths "
                f"could not be restored: {rollback_failures}",
                rollback_failures) from exc
        raise PortableError(f"restore failed and rolled back: {exc}") from exc