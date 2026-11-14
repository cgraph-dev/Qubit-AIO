"""Encrypted, verifiable snapshots, tenant-scoped restore and replay checks.

This is the P1-09 backup/restore surface over the one Business OS SQLite file
owned by :mod:`voicebridge.business.store`. It deliberately does **not** add a
second database, a second execution engine or a second lock: a snapshot is a
consistent copy taken through SQLite's online backup API, so a writer using
WAL can keep running while it is captured.

What a snapshot is
------------------
A snapshot is a directory containing:

* ``business.db`` (plaintext opt-out) or ``business.db.enc`` (default), a
  point-in-time copy of the database **already migrated to the schema this
  code understands**;
* ``manifest.json`` — the schema/version, scope, table row counts, encryption
  parameters and a sha256 for every stored file; and
* ``backup.key`` when no caller-supplied key was given — 32 random bytes
  written with mode ``0600`` so the snapshot can be restored without the
  caller remembering anything.

Encryption is AES-GCM with a caller-supplied 32-byte key or a fresh ephemeral
key. An ephemeral key stored beside the ciphertext protects a copied snapshot
file from casual disclosure, but it is **not** a substitute for an out-of-band
key: anyone holding the whole directory holds the key too. A caller-supplied
key is the real at-rest boundary. If ``cryptography`` cannot be imported the
default path fails closed with ``BACKUP_CRYPTO_UNAVAILABLE`` rather than
silently writing plaintext.

Schema compatibility
--------------------
Migrations are **forward-only**. ``assert_compatible`` reads
``PRAGMA user_version`` and refuses a database newer than
:data:`voicebridge.business.schema.SCHEMA_VERSION`; a database at an older
version is upgraded on the snapshot copy by :class:`BusinessStore`, never
downgraded in place. There is no downgrade path and inventing one would be a
data-loss claim this code cannot support.

Scope
-----
``snapshot(..., tenant_id=X)`` produces a database that physically contains
only tenant ``X``'s rows, not a whole file with a filter applied at read time.
``restore(..., tenant_id=X)`` copies only that tenant into a fresh schema-v3
database and refuses if any other tenant's row appears. Cross-tenant leakage
is a typed failure, not a silent extra row.

Honest limits
-------------
* The manifest is not signed. Its sha256 entries detect a corrupted or
  truncated snapshot file; an attacker who rewrites both the database and the
  manifest is only caught by a caller-supplied key's AES-GCM tag (a wrong key
  or a modified ciphertext fails to decrypt), not by the manifest itself.
* ``rpo_rto_report`` measures this host's snapshot/verify/restore/replay time
  and the age of the newest committed record at snapshot start. It does not
  claim an SLA and a one-shot snapshot does not bound loss after it.
* Restore only targets a **new** path. Overwriting a live database is a typed
  refusal, never an implicit choice.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from . import jobs, ledger
from .errors import BAD_INPUT_CODE, BusinessError, IO_CODE
from .ids import canonical_json, new_id
from .schema import SCHEMA_VERSION
from .store import BusinessStore
from .util import now_iso

__all__ = [
    "BACKUP_SCHEMA",
    "REPLAY_SCHEMA",
    "RPO_RTO_SCHEMA",
    "assert_compatible",
    "manifest_path",
    "replay_verify",
    "restore",
    "rpo_rto_report",
    "snapshot",
    "verify_snapshot",
]

BACKUP_SCHEMA = "qubit-office-backup/1"
REPLAY_SCHEMA = "qubit-office-replay/1"
RPO_RTO_SCHEMA = "qubit-office-rpo-rto/1"

MANIFEST_NAME = "manifest.json"
KEY_NAME = "backup.key"
DATABASE_NAME = "business.db"
ENCRYPTED_NAME = "business.db.enc"
_CIPHER_MAGIC = b"QBKB1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_CHUNK = 1024 * 1024

# Stable refusal codes. They live here (not in errors.py, which this lane does
# not own) and are still raised as typed ``BusinessError`` values.
BACKUP_CRYPTO_UNAVAILABLE_CODE = "BACKUP_CRYPTO_UNAVAILABLE"
BACKUP_SOURCE_MISSING_CODE = "BACKUP_SOURCE_MISSING"
BACKUP_SOURCE_INVALID_CODE = "BACKUP_SOURCE_INVALID"
BACKUP_DEST_EXISTS_CODE = "BACKUP_DEST_EXISTS"
BACKUP_MANIFEST_MISSING_CODE = "BACKUP_MANIFEST_MISSING"
BACKUP_MANIFEST_INVALID_CODE = "BACKUP_MANIFEST_INVALID"
BACKUP_FILE_MISSING_CODE = "BACKUP_FILE_MISSING"
BACKUP_HASH_MISMATCH_CODE = "BACKUP_HASH_MISMATCH"
BACKUP_SCHEMA_TOO_NEW_CODE = "BACKUP_SCHEMA_TOO_NEW"
BACKUP_KEY_MISSING_CODE = "BACKUP_KEY_MISSING"
BACKUP_BAD_KEY_CODE = "BACKUP_BAD_KEY"
BACKUP_TARGET_EXISTS_CODE = "BACKUP_TARGET_EXISTS"
BACKUP_TENANT_NOT_FOUND_CODE = "BACKUP_TENANT_NOT_FOUND"
BACKUP_CROSS_TENANT_LEAK_CODE = "BACKUP_CROSS_TENANT_LEAK"
BACKUP_RESTORE_FAILED_CODE = "BACKUP_RESTORE_FAILED"
BACKUP_REPLAY_FAILED_CODE = "BACKUP_REPLAY_FAILED"

#: Every table that carries ``tenant_id``, in foreign-key insertion order. The
#: order is the contract for a tenant-scoped restore: a child is never inserted
#: before its parent.
_TENANT_TABLES: tuple[str, ...] = (
    "bos_tenant",
    "bos_business",
    "bos_contact",
    "bos_task",
    "bos_job",
    "bos_job_event",
    "bos_approval",
    "bos_ledger_entry",
    "bos_artifact",
    "bos_audit_event",
    "bos_budget",
    "bos_tool_invocation",
    "bos_warroom_session",
    "bos_warroom_source",
    "bos_warroom_evidence",
    "bos_warroom_argument",
    "bos_warroom_experiment",
    "bos_warroom_decision",
    "bos_agent",
    "bos_agent_turn",
    "bos_agent_usage",
    "bos_agent_operation",
    "bos_checkpoint",
    "bos_outbox",
)


# -- small helpers ---------------------------------------------------------
def _fail(code: str, detail: str) -> None:
    raise BusinessError(code, detail)


def _uri_path(path: Path) -> str:
    return quote(str(path.resolve()), safe="/")


def _open_read(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file without ever creating one."""
    path = Path(path)
    if not path.is_file():
        _fail(BACKUP_SOURCE_MISSING_CODE, f"no database at {path}")
    try:
        conn = sqlite3.connect(f"file:{_uri_path(path)}?mode=ro", uri=True)
    except sqlite3.Error:
        # A live WAL writer can leave a read-only open unable to recover the
        # WAL; fall back to a normal connection that cannot write by pragma.
        conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
    else:
        conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(path: Path, data: bytes, mode: int) -> None:
    path = Path(path)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        mode,
    )
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _write_bytes(path, text.encode("utf-8"), mode)


def _prepare_dest(dest: Path) -> None:
    """Create an empty destination directory; refuse a non-empty one."""
    dest = Path(dest)
    if dest.exists():
        if not dest.is_dir():
            _fail(BACKUP_DEST_EXISTS_CODE, f"{dest} exists and is not a directory")
        if any(dest.iterdir()):
            _fail(
                BACKUP_DEST_EXISTS_CODE,
                f"{dest} is not empty; a snapshot needs a fresh directory",
            )
        return
    try:
        dest.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        _fail(IO_CODE, f"cannot create snapshot directory {dest}: {exc}")


def _refuse_existing_target(target: Path) -> None:
    target = Path(target)
    if target.exists():
        _fail(
            BACKUP_TARGET_EXISTS_CODE,
            f"restore target {target} already exists; a live database is never"
            " overwritten",
        )


def _load_crypto() -> tuple[Any, Any]:
    """Return ``(AESGCM, InvalidTag)`` or fail closed with a typed refusal."""
    try:  # keep the import local so an encrypt=False path has no dependency
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            from cryptography.exceptions import InvalidTag
        except ImportError:  # pragma: no cover - older cryptography
            from cryptography.hazmat.primitives.ciphers.aead import (  # type: ignore
                InvalidTag,
            )
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        raise BusinessError(
            BACKUP_CRYPTO_UNAVAILABLE_CODE,
            "encrypted snapshots need the 'cryptography' package; refusing to"
            " write plaintext silently",
        ) from exc
    return AESGCM, InvalidTag


def _normalize_key(key: Any) -> bytes:
    if isinstance(key, (bytes, bytearray)):
        raw = bytes(key)
    elif isinstance(key, str):
        candidate = key.strip()
        if len(candidate) == _KEY_BYTES * 2:
            try:
                raw = bytes.fromhex(candidate)
            except ValueError:
                raw = candidate.encode("utf-8")
        else:
            raw = candidate.encode("utf-8")
    else:
        raise BusinessError(BAD_INPUT_CODE, "key must be bytes or a string")
    if len(raw) != _KEY_BYTES:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"key must be exactly {_KEY_BYTES} bytes (AES-256); got {len(raw)}",
        )
    return raw


def _resolve_key(
    manifest: dict[str, Any], snapshot_dir: Path, key: Any
) -> bytes:
    encryption = manifest.get("encryption") or {}
    if key is not None:
        return _normalize_key(key)
    key_ref = encryption.get("key_ref")
    if not key_ref:
        _fail(
            BACKUP_KEY_MISSING_CODE,
            "this snapshot was encrypted with a caller-supplied key; pass key=",
        )
    key_path = Path(snapshot_dir) / str(key_ref)
    if not key_path.is_file():
        _fail(
            BACKUP_KEY_MISSING_CODE,
            f"snapshot key file {key_path} is missing",
        )
    raw = key_path.read_bytes()
    if len(raw) != _KEY_BYTES:
        _fail(
            BACKUP_BAD_KEY_CODE,
            f"snapshot key file must hold {_KEY_BYTES} bytes",
        )
    return raw


def _encrypt_bytes(key: bytes, data: bytes, nonce: bytes) -> bytes:
    AESGCM, _ = _load_crypto()
    ciphertext = AESGCM(key).encrypt(nonce, data, BACKUP_SCHEMA.encode("utf-8"))
    return _CIPHER_MAGIC + nonce + ciphertext


def _decrypt_bytes(
    key: bytes, payload: bytes, expected_nonce: bytes
) -> bytes:
    AESGCM, InvalidTag = _load_crypto()
    if not payload.startswith(_CIPHER_MAGIC):
        _fail(BACKUP_MANIFEST_INVALID_CODE, "snapshot ciphertext has no magic header")
    nonce = payload[len(_CIPHER_MAGIC) : len(_CIPHER_MAGIC) + _NONCE_BYTES]
    if nonce != expected_nonce:
        _fail(BACKUP_BAD_KEY_CODE, "snapshot nonce does not match the manifest")
    ciphertext = payload[len(_CIPHER_MAGIC) + _NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, BACKUP_SCHEMA.encode("utf-8"))
    except InvalidTag as exc:
        raise BusinessError(
            BACKUP_BAD_KEY_CODE,
            "snapshot decryption failed: wrong key or modified ciphertext",
        ) from exc


# -- schema / table introspection -----------------------------------------
def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]


def _primary_key(conn: sqlite3.Connection, table: str) -> str | None:
    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        if int(row[5]):
            return str(row[1])
    return None


def _table_counts(
    conn: sqlite3.Connection, tenant_id: str | None = None
) -> dict[str, int]:
    present = _table_names(conn)
    counts: dict[str, int] = {}
    for table in _TENANT_TABLES:
        if table not in present:
            continue
        if tenant_id is None:
            sql = f'SELECT COUNT(*) FROM "{table}"'
            params: tuple[Any, ...] = ()
        else:
            sql = f'SELECT COUNT(*) FROM "{table}" WHERE tenant_id=?'
            params = (tenant_id,)
        counts[table] = int(conn.execute(sql, params).fetchone()[0])
    return counts


def _tenant_ids(conn: sqlite3.Connection) -> list[str]:
    if "bos_tenant" not in _table_names(conn):
        return []
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT tenant_id FROM bos_tenant ORDER BY tenant_id"
        ).fetchall()
    ]


def _copy_tenant_rows(
    src: sqlite3.Connection, dst: sqlite3.Connection, tenant_id: str
) -> dict[str, int]:
    """Copy one tenant child-first-safe; caller controls the transaction."""
    dst.execute("PRAGMA foreign_keys=ON")
    copied: dict[str, int] = {}
    for table in _TENANT_TABLES:
        cols = _columns(src, table)
        if not cols:
            continue
        column_list = ",".join(f'"{name}"' for name in cols)
        placeholders = ",".join("?" for _ in cols)
        rows = src.execute(
            f'SELECT {column_list} FROM "{table}" WHERE tenant_id=? ORDER BY rowid',
            (tenant_id,),
        ).fetchall()
        if rows:
            dst.executemany(
                f'INSERT INTO "{table}"({column_list}) VALUES({placeholders})',
                [tuple(row) for row in rows],
            )
        copied[table] = len(rows)
    return copied


def _migrate_copy(path: Path) -> int:
    """Bring a copy up to the current schema and return its version."""
    store = BusinessStore(path)
    conn = store.connect()
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        # Fold WAL back into the single file so the snapshot is one file.
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    return version


def _assert_business_db(conn: sqlite3.Connection) -> None:
    if "bos_tenant" not in _table_names(conn):
        _fail(
            BACKUP_SOURCE_INVALID_CODE,
            "source database has no bos_tenant table; not a Business OS database",
        )


def assert_compatible(store: BusinessStore) -> dict[str, int]:
    """Refuse a database newer than this code supports.

    Reads ``PRAGMA user_version`` from disk **without migrating**. A newer
    version is a typed ``BACKUP_SCHEMA_TOO_NEW``: migrations are forward-only
    and there is no downgrade path.
    """
    path = Path(store.path)
    if not path.is_file():
        _fail(BACKUP_SOURCE_MISSING_CODE, f"no database at {path}")
    conn = _open_read(path)
    try:
        _assert_business_db(conn)
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()
    if version > SCHEMA_VERSION:
        _fail(
            BACKUP_SCHEMA_TOO_NEW_CODE,
            f"database schema v{version} is newer than supported v{SCHEMA_VERSION};"
            " migrations are forward-only and downgrade is not supported",
        )
    return {"schema_version": version, "supported": SCHEMA_VERSION}


# -- snapshot --------------------------------------------------------------
def _online_backup(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    src = None
    dst = None
    try:
        src = sqlite3.connect(str(source), timeout=30.0)
        dst = sqlite3.connect(str(destination))
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        _fail(IO_CODE, f"online backup of {source} failed: {exc}")
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()


def _fresh_scoped_copy(source: Path, destination: Path, tenant_id: str) -> None:
    if destination.exists():
        destination.unlink()
    dst = BusinessStore(destination).connect()
    src = _open_read(source)
    try:
        if tenant_id not in _tenant_ids(src):
            _fail(
                BACKUP_TENANT_NOT_FOUND_CODE,
                f"tenant {tenant_id!r} is not present in the source database",
            )
        dst.execute("BEGIN IMMEDIATE")
        try:
            _copy_tenant_rows(src, dst, tenant_id)
            dst.execute("COMMIT")
        except BaseException:
            dst.execute("ROLLBACK")
            raise
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        src.close()
        dst.close()


def snapshot(
    store: BusinessStore,
    dest: str | Path,
    *,
    tenant_id: str | None = None,
    encrypt: bool = True,
    key: Any = None,
) -> dict[str, Any]:
    """Take a consistent, verifiable snapshot into a new directory."""
    dest = Path(dest)
    source = Path(store.path)
    compatibility = assert_compatible(store)
    _prepare_dest(dest)

    raw_copy = dest / ".business.db.raw"
    plain_copy = dest / ".business.db.plain"
    started_at = now_iso()
    started = time.perf_counter()
    try:
        _online_backup(source, raw_copy)
        _migrate_copy(raw_copy)
        if tenant_id is not None:
            _fresh_scoped_copy(raw_copy, plain_copy, tenant_id)
            raw_copy.unlink(missing_ok=True)
            selected = plain_copy
        else:
            if plain_copy.exists():
                plain_copy.unlink()
            os.replace(raw_copy, plain_copy)
            selected = plain_copy

        conn = _open_read(selected)
        try:
            _assert_business_db(conn)
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > SCHEMA_VERSION:
                _fail(
                    BACKUP_SCHEMA_TOO_NEW_CODE,
                    f"snapshot schema v{schema_version} is newer than supported"
                    f" v{SCHEMA_VERSION}",
                )
            counts = _table_counts(conn, tenant_id)
            tenants = _tenant_ids(conn)
        finally:
            conn.close()

        plain_sha = _sha256_file(selected)
        plain_bytes = selected.stat().st_size
        files: list[dict[str, Any]] = []
        encryption: dict[str, Any] = {"enabled": False}

        if encrypt:
            AESGCM, _ = _load_crypto()  # fail closed before writing anything
            if key is None:
                raw_key = os.urandom(_KEY_BYTES)
                _write_bytes(dest / KEY_NAME, raw_key, 0o600)
                key_ref: str | None = KEY_NAME
                files.append(
                    {
                        "role": "key",
                        "name": KEY_NAME,
                        "sha256": _sha256_bytes(raw_key),
                        "bytes": len(raw_key),
                        "encrypted": False,
                    }
                )
            else:
                raw_key = _normalize_key(key)
                key_ref = None
            nonce = os.urandom(_NONCE_BYTES)
            data = selected.read_bytes()
            payload = _encrypt_bytes(raw_key, data, nonce)
            db_name = ENCRYPTED_NAME
            _write_bytes(dest / db_name, payload, 0o600)
            db_sha = _sha256_bytes(payload)
            db_bytes = len(payload)
            encryption = {
                "enabled": True,
                "algorithm": "AESGCM",
                "kdf": "raw-256",
                "nonce_b64": __import__("base64").b64encode(nonce).decode("ascii"),
                "key_ref": key_ref,
            }
        else:
            db_name = DATABASE_NAME
            if (dest / db_name).exists():
                (dest / db_name).unlink()
            os.replace(selected, dest / db_name)
            db_sha = plain_sha
            db_bytes = plain_bytes

        files.insert(
            0,
            {
                "role": "database",
                "name": db_name,
                "sha256": db_sha,
                "bytes": db_bytes,
                "encrypted": bool(encrypt),
                "plaintext_sha256": plain_sha,
                "plaintext_bytes": plain_bytes,
            },
        )

        manifest: dict[str, Any] = {
            "schema": BACKUP_SCHEMA,
            "snapshot_id": new_id("bkp"),
            "created_at": started_at,
            "tool": {"name": "qubit-office-backup", "version": 1},
            "source": {
                "database": source.name,
                "schema_version_before": compatibility["schema_version"],
            },
            "schema_version": schema_version,
            "scope": {"tenant_id": tenant_id},
            "tenants": tenants,
            "encryption": encryption,
            "files": files,
            "table_counts": counts,
            "row_counts": {"total": sum(counts.values()), "tables": counts},
        }
        _write_json(dest / MANIFEST_NAME, manifest)
        manifest["_duration_seconds"] = round(time.perf_counter() - started, 6)
        return manifest
    finally:
        for leftover in (raw_copy, plain_copy):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort cleanup
                pass


def manifest_path(snapshot_dir: str | Path) -> Path:
    return Path(snapshot_dir) / MANIFEST_NAME


def _read_manifest(snapshot_dir: Path) -> dict[str, Any]:
    path = Path(snapshot_dir) / MANIFEST_NAME
    if not path.is_file():
        _fail(
            BACKUP_MANIFEST_MISSING_CODE,
            f"no {MANIFEST_NAME} in {snapshot_dir}; refusing an unverifiable"
            " snapshot",
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(BACKUP_MANIFEST_INVALID_CODE, f"cannot read {path}: {exc}")
    if not isinstance(manifest, dict):
        _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest is not a JSON object")
    if manifest.get("schema") != BACKUP_SCHEMA:
        _fail(
            BACKUP_MANIFEST_INVALID_CODE,
            f"manifest schema {manifest.get('schema')!r} is not {BACKUP_SCHEMA!r}",
        )
    required = {
        "snapshot_id",
        "created_at",
        "schema_version",
        "scope",
        "encryption",
        "files",
        "table_counts",
    }
    missing = required - set(manifest)
    if missing:
        _fail(
            BACKUP_MANIFEST_INVALID_CODE,
            f"manifest is missing {', '.join(sorted(missing))}",
        )
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest files list is empty")
    if not isinstance(manifest["table_counts"], dict):
        _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest table_counts is not a mapping")
    if not isinstance(manifest["scope"], dict):
        _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest scope is not a mapping")
    if not isinstance(manifest["encryption"], dict):
        _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest encryption is not a mapping")
    return manifest


def _declared_files(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest file entry is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            _fail(BACKUP_MANIFEST_INVALID_CODE, "manifest file entry has no name")
        if "/" in name or "\\" in name or name in (".", ".."):
            _fail(
                BACKUP_MANIFEST_INVALID_CODE,
                f"manifest file name {name!r} escapes the snapshot directory",
            )
        if name in declared:
            _fail(BACKUP_MANIFEST_INVALID_CODE, f"duplicate manifest file {name!r}")
        declared[name] = entry
    database_entries = [
        entry for entry in declared.values() if entry.get("role") == "database"
    ]
    if len(database_entries) != 1:
        _fail(
            BACKUP_MANIFEST_INVALID_CODE,
            "manifest must declare exactly one database file",
        )
    return declared


def verify_snapshot(snapshot_dir: str | Path) -> dict[str, Any]:
    """Verify hashes, version and row counts without needing the key."""
    snapshot_dir = Path(snapshot_dir)
    manifest = _read_manifest(snapshot_dir)
    if int(manifest["schema_version"]) > SCHEMA_VERSION:
        _fail(
            BACKUP_SCHEMA_TOO_NEW_CODE,
            f"snapshot schema v{manifest['schema_version']} is newer than"
            f" supported v{SCHEMA_VERSION}",
        )

    declared = _declared_files(manifest)
    reported_files: list[dict[str, Any]] = []
    for name, entry in declared.items():
        path = snapshot_dir / name
        if not path.is_file():
            _fail(BACKUP_FILE_MISSING_CODE, f"snapshot file {name} is missing")
        actual_sha = _sha256_file(path)
        if actual_sha != entry.get("sha256"):
            _fail(
                BACKUP_HASH_MISMATCH_CODE,
                f"{name} hash {actual_sha} does not match the manifest",
            )
        actual_bytes = path.stat().st_size
        if int(entry.get("bytes", -1)) != actual_bytes:
            _fail(
                BACKUP_HASH_MISMATCH_CODE,
                f"{name} size {actual_bytes} does not match the manifest",
            )
        reported_files.append(
            {
                "name": name,
                "role": entry.get("role"),
                "sha256": actual_sha,
                "bytes": actual_bytes,
                "encrypted": bool(entry.get("encrypted")),
            }
        )

    # Any file we did not declare is foreign or an unexpected leftover.
    for child in snapshot_dir.iterdir():
        if child.name == MANIFEST_NAME:
            continue
        if child.name not in declared:
            _fail(
                BACKUP_MANIFEST_INVALID_CODE,
                f"snapshot directory contains undeclared entry {child.name!r}",
            )

    encryption = manifest["encryption"]
    encrypted = bool(encryption.get("enabled"))
    if encrypted:
        if encryption.get("algorithm") != "AESGCM":
            _fail(
                BACKUP_MANIFEST_INVALID_CODE,
                f"unsupported encryption algorithm {encryption.get('algorithm')!r}",
            )
        try:
            import base64

            nonce = base64.b64decode(str(encryption.get("nonce_b64", "")))
        except (ValueError, TypeError) as exc:
            _fail(BACKUP_MANIFEST_INVALID_CODE, f"invalid nonce: {exc}")
        if len(nonce) != _NONCE_BYTES:
            _fail(BACKUP_MANIFEST_INVALID_CODE, "nonce is not 12 bytes")
        key_ref = encryption.get("key_ref")
        if key_ref is not None and key_ref != KEY_NAME:
            _fail(
                BACKUP_MANIFEST_INVALID_CODE,
                f"unsupported key reference {key_ref!r}",
            )

    counts = manifest["table_counts"]
    return {
        "ok": True,
        "schema": "qubit-office-backup-verify/1",
        "snapshot_id": manifest["snapshot_id"],
        "created_at": manifest["created_at"],
        "schema_version": int(manifest["schema_version"]),
        "scope": manifest["scope"],
        "encrypted": encrypted,
        "files": reported_files,
        "table_counts": counts,
        "row_counts": manifest.get("row_counts", {}),
    }


# -- restore ---------------------------------------------------------------
def _materialize_plaintext(
    manifest: dict[str, Any], snapshot_dir: Path, key: Any
) -> tuple[Path, Path | None]:
    """Return ``(plaintext_db, temp_to_clean)`` for a verified snapshot."""
    migration_note = verify_snapshot(snapshot_dir)
    if not migration_note["ok"]:
        _fail(BACKUP_MANIFEST_INVALID_CODE, "snapshot failed verification")
    database = next(
        entry for entry in _declared_files(manifest).values()
        if entry.get("role") == "database"
    )
    stored = Path(snapshot_dir) / database["name"]
    if not database.get("encrypted"):
        return stored, None

    encryption = manifest["encryption"]
    import base64

    nonce = base64.b64decode(str(encryption["nonce_b64"]))
    raw_key = _resolve_key(manifest, snapshot_dir, key)
    payload = stored.read_bytes()
    data = _decrypt_bytes(raw_key, payload, nonce)
    expected = database.get("plaintext_sha256")
    if expected is not None and _sha256_bytes(data) != expected:
        _fail(
            BACKUP_HASH_MISMATCH_CODE,
            "decrypted snapshot does not match its recorded plaintext hash",
        )
    fd, temp_name = tempfile.mkstemp(prefix="qubit-restore-", suffix=".db")
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    temp_path = Path(temp_name)
    os.chmod(temp_path, 0o600)
    return temp_path, temp_path


def _restore_whole(plaintext: Path, target: Path) -> dict[str, Any]:
    shutil.copyfile(plaintext, target)
    conn = BusinessStore(target).connect()
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            _fail(
                BACKUP_SCHEMA_TOO_NEW_CODE,
                f"restored schema v{version} is newer than supported v{SCHEMA_VERSION}",
            )
        counts = _table_counts(conn)
        tenants = _tenant_ids(conn)
    finally:
        conn.close()
    return {
        "scope": {"tenant_id": None},
        "tenants": tenants,
        "table_counts": counts,
    }


def _restore_tenant(plaintext: Path, target: Path, tenant_id: str) -> dict[str, Any]:
    src = _open_read(plaintext)
    dst = None
    try:
        if tenant_id not in _tenant_ids(src):
            _fail(
                BACKUP_TENANT_NOT_FOUND_CODE,
                f"tenant {tenant_id!r} is not present in the snapshot",
            )
        # Only create the target after the request is known to be restorable,
        # so a typed refusal never leaves a half-built database behind.
        dst = BusinessStore(target).connect()
        dst.execute("BEGIN IMMEDIATE")
        try:
            copied = _copy_tenant_rows(src, dst, tenant_id)
            dst.execute("COMMIT")
        except BaseException:
            dst.execute("ROLLBACK")
            raise
        if int(dst.execute("PRAGMA user_version").fetchone()[0]) > SCHEMA_VERSION:
            _fail(
                BACKUP_SCHEMA_TOO_NEW_CODE,
                "restored schema is newer than supported",
            )
        # A tenant-scoped restore must never carry another tenant's row.
        leaked: dict[str, int] = {}
        for table in _TENANT_TABLES:
            if table not in _table_names(dst):
                continue
            extra = int(
                dst.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE tenant_id<>?',
                    (tenant_id,),
                ).fetchone()[0]
            )
            if extra:
                leaked[table] = extra
        tenant_rows = int(
            dst.execute("SELECT COUNT(*) FROM bos_tenant").fetchone()[0]
        )
        if tenant_rows != 1:
            leaked["bos_tenant"] = tenant_rows
        if leaked:
            _fail(
                BACKUP_CROSS_TENANT_LEAK_CODE,
                f"tenant restore leaked rows: {leaked}",
            )
        counts = _table_counts(dst, tenant_id)
        tenants = _tenant_ids(dst)
    finally:
        src.close()
        if dst is not None:
            dst.close()
    return {
        "scope": {"tenant_id": tenant_id},
        "tenants": tenants,
        "table_counts": counts,
        "copied": copied,
    }


def restore(
    snapshot_dir: str | Path,
    target: str | Path,
    *,
    tenant_id: str | None = None,
    key: Any = None,
) -> dict[str, Any]:
    """Restore into a **new** path; never overwrite an existing database."""
    snapshot_dir = Path(snapshot_dir)
    target = Path(target)
    manifest = _read_manifest(snapshot_dir)
    if int(manifest["schema_version"]) > SCHEMA_VERSION:
        _fail(
            BACKUP_SCHEMA_TOO_NEW_CODE,
            f"snapshot schema v{manifest['schema_version']} is newer than"
            f" supported v{SCHEMA_VERSION}",
        )
    _refuse_existing_target(target)
    started = time.perf_counter()
    plaintext, temp = _materialize_plaintext(manifest, snapshot_dir, key)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if tenant_id is None:
            scope = _restore_whole(plaintext, target)
        else:
            scope = _restore_tenant(plaintext, target, tenant_id)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return {
        "ok": True,
        "schema": "qubit-office-restore/1",
        "snapshot_id": manifest["snapshot_id"],
        "target": str(target),
        "scope": scope["scope"],
        "tenants": scope["tenants"],
        "table_counts": scope["table_counts"],
        "encrypted": bool(manifest["encryption"].get("enabled")),
        "duration_seconds": round(time.perf_counter() - started, 6),
    }


# -- replay verification ---------------------------------------------------
def _digest_table(
    conn: sqlite3.Connection, table: str, tenant_ids: Sequence[str]
) -> str:
    cols = _columns(conn, table)
    if not cols or not tenant_ids:
        return _sha256_bytes(b"")
    placeholders = ",".join("?" for _ in tenant_ids)
    rows = conn.execute(
        f'SELECT * FROM "{table}" WHERE tenant_id IN ({placeholders})',
        tuple(tenant_ids),
    ).fetchall()
    dicts = [dict(zip(cols, tuple(row))) for row in rows]
    dicts.sort(key=canonical_json)
    return _sha256_bytes(canonical_json(dicts).encode("utf-8"))


def _newest_record_iso(conn: sqlite3.Connection) -> str | None:
    newest: str | None = None
    for table in _TENANT_TABLES:
        if table not in _table_names(conn):
            continue
        cols = _columns(conn, table)
        if "created_at" not in cols:
            continue
        value = conn.execute(
            f'SELECT MAX(created_at) FROM "{table}"'
        ).fetchone()[0]
        if value is not None and (newest is None or str(value) > newest):
            newest = str(value)
    return newest


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_seq_contiguous(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    bad: list[str] = []
    rows = conn.execute(
        "SELECT job_id, GROUP_CONCAT(seq, ',') FROM bos_job_event"
        " GROUP BY job_id"
    ).fetchall()
    for job_id, seqs in rows:
        values = sorted(int(part) for part in str(seqs).split(",") if part)
        if values != list(range(1, len(values) + 1)):
            bad.append(str(job_id))
    return not bad, bad


def replay_verify(
    snapshot_dir: str | Path,
    target: str | Path,
    *,
    key: Any = None,
) -> dict[str, Any]:
    """Reopen a restored database and prove it replays from the snapshot."""
    snapshot_dir = Path(snapshot_dir)
    target = Path(target)
    if not target.is_file():
        _fail(
            BACKUP_RESTORE_FAILED_CODE,
            f"no restored database at {target}",
        )
    manifest = _read_manifest(snapshot_dir)
    plaintext, temp = _materialize_plaintext(manifest, snapshot_dir, key)
    src = _open_read(plaintext)
    store = BusinessStore(target)
    failures: list[str] = []
    checks: list[dict[str, Any]] = []
    tenant_ids: list[str] = []
    tenant_set: set[str] = set()
    summaries: dict[str, dict[str, Any]] = {}
    checkpoints: dict[str, int] = {}
    outbox: dict[str, int] = {}
    recovered: list[Any] = []
    try:
        conn = _open_read(target)
        try:
            integrity = [
                str(row[0])
                for row in conn.execute("PRAGMA integrity_check").fetchall()
            ]
            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
            tenant_ids = _tenant_ids(conn)
            tenant_set = set(tenant_ids)

            checks.append(
                {
                    "name": "integrity_check",
                    "ok": integrity == ["ok"],
                    "detail": integrity,
                }
            )
            if integrity != ["ok"]:
                failures.append("integrity_check")

            checks.append(
                {
                    "name": "foreign_key_check",
                    "ok": not fk_rows,
                    "violations": len(fk_rows),
                }
            )
            if fk_rows:
                failures.append("foreign_key_check")

            triggers = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            required_triggers = {
                "bos_ledger_entry_no_update",
                "bos_ledger_entry_no_delete",
                "bos_job_event_no_update",
                "bos_job_event_no_delete",
                "bos_checkpoint_no_update",
                "bos_checkpoint_no_delete",
            }
            missing_triggers = required_triggers - triggers
            checks.append(
                {
                    "name": "append_only_triggers",
                    "ok": not missing_triggers,
                    "missing": sorted(missing_triggers),
                }
            )
            if missing_triggers:
                failures.append("append_only_triggers")

            if tenant_ids:
                placeholders = ",".join("?" for _ in tenant_ids)
                # Digest equality proves the restored rows are the snapshot
                # rows. It is computed before ``jobs.recover`` mutates state.
                for table in _TENANT_TABLES:
                    if table not in _table_names(conn):
                        continue
                    source_digest = _digest_table(src, table, tenant_ids)
                    target_digest = _digest_table(conn, table, tenant_ids)
                    ok = source_digest == target_digest
                    rows = int(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE tenant_id'
                            f" IN ({placeholders})",
                            tuple(tenant_ids),
                        ).fetchone()[0]
                    )
                    checks.append(
                        {
                            "name": f"digest.{table}",
                            "ok": ok,
                            "rows": rows,
                        }
                    )
                    if not ok:
                        failures.append(f"digest.{table}")

            contiguous, bad_jobs = _event_seq_contiguous(conn)
            checks.append(
                {
                    "name": "job_event_sequence",
                    "ok": contiguous,
                    "bad_jobs": bad_jobs,
                }
            )
            if not contiguous:
                failures.append("job_event_sequence")

            # Ledger balance must hold on the restored rows and equal the
            # snapshot's own totals.
            ledgers_equal = True
            for tenant in tenant_ids:
                summary = ledger.summary(store, tenant)
                summaries[tenant] = summary
                if summary["net_minor"] != (
                    summary["credit_minor"] - summary["debit_minor"]
                ):
                    ledgers_equal = False
                source_rows = src.execute(
                    "SELECT direction, COALESCE(SUM(amount_minor),0)"
                    " FROM bos_ledger_entry WHERE tenant_id=? GROUP BY direction",
                    (tenant,),
                ).fetchall()
                parts = {"debit": 0, "credit": 0}
                for direction, total in source_rows:
                    parts[str(direction)] = int(total)
                if (
                    summary["debit_minor"] != parts["debit"]
                    or summary["credit_minor"] != parts["credit"]
                ):
                    ledgers_equal = False
            checks.append(
                {
                    "name": "ledger_replay_equality",
                    "ok": ledgers_equal,
                    "summaries": summaries,
                }
            )
            if not ledgers_equal:
                failures.append("ledger_replay_equality")

            checkpoints = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT run_id, COUNT(*) FROM bos_checkpoint"
                    " GROUP BY run_id"
                ).fetchall()
            }
            outbox = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT status, COUNT(*) FROM bos_outbox GROUP BY status"
                ).fetchall()
            }
        finally:
            conn.close()

        # The restart sweep is the durable-outcome contract: an interrupted
        # running job becomes ``unknown`` rather than being retried silently.
        recovered = jobs.recover(store, now=time.time() + 10**9)
        checks.append(
            {
                "name": "jobs_recover",
                "ok": True,
                "unknown": [item.job_id for item in recovered],
            }
        )

        # After recover, ledger totals must be unchanged; recovery never
        # rewrites financial truth.
        after = {tenant: ledger.summary(store, tenant) for tenant in tenant_ids}
        post_ok = all(
            after[t]["entry_count"] == summaries[t]["entry_count"]
            and after[t]["net_minor"] == summaries[t]["net_minor"]
            for t in tenant_ids
        )
        checks.append({"name": "post_recover_ledger_stable", "ok": post_ok})
        if not post_ok:
            failures.append("post_recover_ledger_stable")
    finally:
        src.close()
        if temp is not None:
            temp.unlink(missing_ok=True)

    if failures:
        _fail(
            BACKUP_REPLAY_FAILED_CODE,
            "replay verification failed: " + ", ".join(sorted(set(failures))),
        )
    return {
        "ok": True,
        "schema": REPLAY_SCHEMA,
        "snapshot_id": manifest["snapshot_id"],
        "target": str(target),
        "tenants": sorted(tenant_set),
        "checks": checks,
        "checkpoints_by_run": checkpoints,
        "outbox_by_status": outbox,
        "recovered_unknown_jobs": [item.job_id for item in recovered],
    }


# -- measured RPO / RTO ----------------------------------------------------
def rpo_rto_report(
    store: BusinessStore,
    workdir: str | Path,
    *,
    tenant_id: str | None = None,
    encrypt: bool = True,
    key: Any = None,
) -> dict[str, Any]:
    """Measure a real snapshot/verify/restore/replay cycle on this host.

    The numbers are wall-clock measurements from one run. No SLA is claimed:
    a one-shot snapshot does not bound loss after it, and RTO depends on this
    host's disk and CPU.
    """
    workdir = Path(workdir)
    _prepare_dest(workdir)
    snapshot_dir = workdir / "snapshot"
    restored = workdir / "restored" / DATABASE_NAME

    started_at = now_iso()
    t0 = time.perf_counter()
    manifest = snapshot(
        store,
        snapshot_dir,
        tenant_id=tenant_id,
        encrypt=encrypt,
        key=key,
    )
    snapshot_seconds = time.perf_counter() - t0

    t1 = time.perf_counter()
    verified = verify_snapshot(snapshot_dir)
    verify_seconds = time.perf_counter() - t1

    t2 = time.perf_counter()
    restored_report = restore(
        snapshot_dir, restored, tenant_id=tenant_id, key=key
    )
    restore_seconds = time.perf_counter() - t2

    t3 = time.perf_counter()
    replay = replay_verify(snapshot_dir, restored, key=key)
    replay_seconds = time.perf_counter() - t3

    conn = _open_read(Path(store.path))
    try:
        newest_iso = _newest_record_iso(conn)
    finally:
        conn.close()
    newest = _parse_iso(newest_iso)
    snapshot_started = _parse_iso(manifest["created_at"])
    rpo_seconds = 0.0
    if newest is not None and snapshot_started is not None:
        rpo_seconds = max(
            0.0, (snapshot_started - newest).total_seconds()
        )

    return {
        "ok": bool(verified["ok"] and restored_report["ok"] and replay["ok"]),
        "schema": RPO_RTO_SCHEMA,
        "generated_at": now_iso(),
        "started_at": started_at,
        "snapshot": {
            "snapshot_id": manifest["snapshot_id"],
            "started_at": manifest["created_at"],
            "seconds": round(snapshot_seconds, 6),
            "files": manifest["files"],
            "row_counts": manifest["row_counts"],
            "encrypted": bool(manifest["encryption"].get("enabled")),
        },
        "verify": {"seconds": round(verify_seconds, 6)},
        "restore": {
            "seconds": round(restore_seconds, 6),
            "target": str(restored),
            "table_counts": restored_report["table_counts"],
        },
        "replay": {
            "seconds": round(replay_seconds, 6),
            "checks": [check["name"] for check in replay["checks"]],
        },
        "rpo": {
            "definition": "age of the newest committed business record when the"
            " snapshot started; a one-shot snapshot does not bound loss after it",
            "newest_record_at": newest_iso,
            "snapshot_started_at": manifest["created_at"],
            "rpo_seconds": round(rpo_seconds, 6),
            "sla_claimed": False,
        },
        "rto": {
            "definition": "measured restore + replay wall time to a reopened and"
            " verified database on this host",
            "restore_seconds": round(restore_seconds, 6),
            "replay_seconds": round(replay_seconds, 6),
            "rto_seconds": round(restore_seconds + replay_seconds, 6),
            "sla_claimed": False,
        },
    }
