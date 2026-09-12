"""Packet 8C: professional report bundles, store, and rendering.

The canonical JSON manifest is the single source of truth for each report;
Markdown and a self-contained, responsive, print-friendly HTML are derived
deterministically from it. Bundles are written atomically under a private root
(``<state_dir>/reports/<report_id>/``) with ``0700`` directories and ``0600``
files, validated (schema, hashes, links, MIME) before being published, and
served only through authenticated header-token routes.

Security rules enforced here:

- every message/page-derived value is HTML-escaped / Markdown-escaped before it
  reaches a bundle (no raw HTML/Markdown injection);
- source links are restricted to the approved https hosts; anything else is
  dropped and rendered as "unsafe source";
- report/asset IDs are strict, and file serving resolves realpaths so
  ``..``/symlink escapes are refused;
- bundles never contain cookies, tokens, secrets, filesystem paths, or raw
  message bodies;
- legacy Markdown in the root is indexed as clearly labelled legacy data and is
  never deleted; retention prunes only expired bundles, never cursors, memory,
  evidence, legacy reports, or user data.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .contracts import (AccountHealthStatus, CommunicationItem,
                        EvidenceAssetRecord, EvidenceStatus, IdentitySource,
                        Platform, ReadState, ReportManifest, ReportStatus,
                        Sensitivity)
from .store import CommunicationsStore
from ..redaction import redact_text

REPORT_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")
ASSET_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")
LEGACY_MD_RE = re.compile(r"^[A-Za-z0-9 _.#-]+\.md$")
ALLOWED_SOURCE_HOSTS = {"mail.google.com", "www.instagram.com",
                        "www.messenger.com"}
SUMMARY_MAX = 600
PREVIEW_MAX = 200
MAX_ASSET_BYTES = 5 * 1024 * 1024   # reject oversized evidence assets (5 MiB)


class ReportStoreError(Exception):
    """Typed report-store failure (never a silent pass)."""


class ReportValidationError(ReportStoreError):
    """A bundle failed validation before being published."""


def _atomic_write_text(path: Path, text: str) -> None:
    import tempfile as _tf
    fd, tmp = _tf.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                          suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fsync(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _chmod_tree(root: Path) -> None:
    for p in [root] + sorted(root.rglob("*"), reverse=True):
        try:
            os.chmod(p, 0o600 if p.is_file() else 0o700)
        except OSError:
            pass


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime(
            "%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return str(ts)[:19]


def _md_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\\", "\\\\").replace("[", "\\[")
            .replace("]", "\\]").replace("*", "\\*").replace("_", "\\_")
            .replace("`", "\\`").replace("#", "\\#").replace("\n", " ")
            .replace("|", "\\|"))


def safe_source_link(url: str) -> Optional[str]:
    """Return a safe https source URL or None (malicious/unsafe/secret-bearing
    dropped)."""
    if not url or len(url) > 2000:
        return None
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.scheme != "https":
        return None
    host = (u.hostname or "").lower()
    if host not in ALLOWED_SOURCE_HOSTS:
        return None
    if redact_text(url) != url:
        return None   # secret-bearing URL (token/key/... in query) rejected
    return url


def valid_asset_mime(mime: str) -> bool:
    return mime in ("image/webp", "image/jpeg", "image/png")


def build_manifest_payload(manifest: ReportManifest, items, assets,
                           account_health: Optional[Dict[str, Any]] = None,
                           acknowledged: bool = False) -> Dict[str, Any]:
    """Canonical JSON payload. Message-derived fields are BOUNDED; raw bodies
    are never included."""
    return {
        "schema_version": manifest.schema_version,
        "report_id": manifest.report_id,
        "created_at": manifest.created_at,
        "window_start": manifest.window_start,
        "window_end": manifest.window_end,
        "status": manifest.status.value,
        "new_count": manifest.new_count,
        "generated_analysis": bool(manifest.generated_analysis),
        "analysis_disclaimer": manifest.analysis_disclaimer,
        "retention_days": manifest.retention_days,
        "summary": redact_text(manifest.summary[:SUMMARY_MAX]),
        "platforms": sorted(set(manifest.platforms)),
        "accounts": sorted(set(manifest.accounts)),
        "partial_failures": list(manifest.partial_failures),
        "acknowledged": bool(acknowledged),
        "account_health": dict(account_health or {}),
        "items": [
            {
                "identity": it.identity,
                "platform": it.platform.value,
                "account_scope": it.account_scope,
                "sender": redact_text(it.sender[:80]),
                "display_name": redact_text(it.display_name[:80]),
                "subject": redact_text(it.subject[:240]),
                "preview": redact_text(it.preview[:PREVIEW_MAX]),
                "ts_normalized": it.ts_normalized,
                "ts_raw": redact_text(it.ts_raw[:60]),
                "read_state": it.read_state.value,
                "is_new": bool(it.is_new),
                "safe_url": safe_source_link(it.safe_url),
                "captured_at": it.captured_at,
                "evidence_status": it.evidence_status.value,
                "adapter_version": it.adapter_version,
            } for it in items
        ],
        "assets": [
            {
                "asset_id": a.asset_id,
                "item_identity": a.item_identity,
                "mime": a.mime,
                "byte_count": a.byte_count,
                "sha256": a.sha256,
                "captured_at": a.captured_at,
                "redaction_status": a.redaction_status.value,
                "width": a.width,
                "height": a.height,
            } for a in assets
        ],
    }


class ReportStore:
    """Private, atomic bundle store under a root directory."""

    def __init__(self, root: Path | str, *, private_files: bool = True) -> None:
        self._root = Path(root)
        self._private_files = bool(private_files)
        if private_files:
            self._root.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._root, 0o700)
            except OSError:
                pass
        self._cleanup_stages()

    # ------------------------------------------------------------- validation
    @staticmethod
    def validate_report_id(report_id: str) -> str:
        if not REPORT_ID_RE.match(str(report_id or "")):
            raise ReportStoreError(f"invalid report id: {report_id!r}")
        return report_id

    @staticmethod
    def validate_asset_id(asset_id: str) -> str:
        if not ASSET_ID_RE.match(str(asset_id or "")):
            raise ReportStoreError(f"invalid asset id: {asset_id!r}")
        return asset_id

    def _validate_assets(self, assets, asset_bytes) -> None:
        for a in assets:
            self.validate_asset_id(a.asset_id)
            if not valid_asset_mime(a.mime):
                raise ReportValidationError(
                    f"asset {a.asset_id}: unsupported MIME {a.mime!r}")
            if a.byte_count and a.byte_count > MAX_ASSET_BYTES:
                raise ReportValidationError(
                    f"asset {a.asset_id}: oversized ({a.byte_count} bytes)")
            data = (asset_bytes or {}).get(a.asset_id)
            if data is None:
                continue   # missing bytes -> explicit missing-evidence reason
            if len(data) > MAX_ASSET_BYTES:
                raise ReportValidationError(
                    f"asset {a.asset_id}: oversized data")
            digest = hashlib.sha256(data).hexdigest()
            if a.sha256 and digest != a.sha256:
                raise ReportValidationError(
                    f"asset {a.asset_id}: sha256 mismatch")
            if a.byte_count and len(data) != a.byte_count:
                raise ReportValidationError(
                    f"asset {a.asset_id}: byte count mismatch")

    def _validate_bundle(self, stage: Path, payload: Dict[str, Any],
                         written: Dict[str, str]) -> None:
        mf = json.loads((stage / "manifest.json").read_text())
        if mf.get("report_id") != payload.get("report_id"):
            raise ReportValidationError("manifest report_id mismatch")
        if int(mf.get("schema_version") or 0) < 1:
            raise ReportValidationError("manifest schema_version invalid")
        for a in payload.get("assets", []):
            if a["asset_id"] in written:
                if a["sha256"] != written[a["asset_id"]]:
                    raise ReportValidationError(
                        f"asset {a['asset_id']} hash changed on write")
                p = stage / "assets" / f"{a['asset_id']}.webp"
                if not p.exists():
                    raise ReportValidationError(
                        f"asset {a['asset_id']} missing after write")
        if not (stage / "report.md").exists() or \
                not (stage / "index.html").exists():
            raise ReportValidationError("bundle missing md/html")

    # -------------------------------------------------------------- rendering
    def render(self, manifest: ReportManifest, items, assets, *,
               asset_bytes: Optional[Dict[str, bytes]] = None,
               account_health: Optional[Dict[str, Any]] = None,
               acknowledged: bool = False,
               keep_backup: bool = False) -> Dict[str, Any]:
        """Write the canonical bundle atomically. `asset_bytes` maps asset_id to
        bytes; assets without bytes are recorded with a typed missing-evidence
        reason. Returns the published bundle metadata."""
        if not isinstance(manifest, ReportManifest):
            raise ReportStoreError("manifest must be a ReportManifest")
        report_id = self.validate_report_id(manifest.report_id)
        self._validate_assets(assets, asset_bytes)
        payload = build_manifest_payload(manifest, items, assets,
                                         account_health=account_health,
                                         acknowledged=acknowledged)
        stage = self._root / f".stage-{report_id}-{uuid.uuid4().hex[:8]}"
        assets_dir = stage / "assets"
        assets_dir.mkdir(parents=True)
        written: Dict[str, str] = {}
        try:
            for a in assets:
                data = (asset_bytes or {}).get(a.asset_id)
                if data is None:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                target = assets_dir / f"{a.asset_id}.webp"
                target.write_bytes(data)
                written[a.asset_id] = digest
            (stage / "manifest.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False))
            (stage / "report.md").write_text(self.render_markdown(payload))
            (stage / "index.html").write_text(self.render_html(payload))
            for p in sorted(stage.rglob("*")):
                _fsync(p)
            self._validate_bundle(stage, payload, written)
            self._publish(stage, report_id, keep_backup=keep_backup)
        except Exception:
            try:
                shutil.rmtree(stage)   # failed render: remove the stage dir
            except OSError:
                pass   # a leftover .stage-* dir is cleaned by a later init
            raise
        return {"report_id": report_id,
                "items": len(payload["items"]),
                "assets_written": len(written),
                "assets_total": len(payload["assets"]),
                "status": payload["status"]}

    def _publish(self, stage: Path, report_id: str, *, keep_backup: bool) -> None:
        final = self._root / report_id
        backup = None
        if final.exists():
            backup = self._root / f"{report_id}.bak-{int(time.time())}"
            os.replace(final, backup)
        try:
            os.replace(stage, final)
        except Exception:
            if backup is not None and backup.exists() and not final.exists():
                os.replace(backup, final)
            raise
        if keep_backup:
            return
        if backup is not None:
            # old-version backup cleanup: on failure the backup is RETAINED
            # (fail-safe) — never ignore_errors on a live report deletion.
            try:
                shutil.rmtree(backup)
            except OSError:
                pass
        if self._private_files:
            _chmod_tree(final)

    def _cleanup_stages(self) -> None:
        for d in list(self._root.iterdir()):
            if d.name.startswith(".stage-") and d.is_dir() and not d.is_symlink():
                try:
                    shutil.rmtree(d)
                except OSError:
                    pass   # leftover stage retained; a later init retries

    # ------------------------------------------------------- index / serving
    def bundle_dir(self, report_id: str) -> Path:
        return self._root / self.validate_report_id(report_id)

    def _entry(self, report_id: str) -> Optional[Dict[str, Any]]:
        d = self.bundle_dir(report_id)
        mf = d / "manifest.json"
        if not mf.exists():
            return None
        try:
            payload = json.loads(mf.read_text())
        except Exception:
            return None
        assets = payload.get("assets", [])
        available = sum(1 for a in assets
                        if a.get("redaction_status") == "available")
        return {
            "report_id": report_id,
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
            "new_count": payload.get("new_count", 0),
            "platforms": payload.get("platforms", []),
            "accounts": payload.get("accounts", []),
            "summary": (payload.get("summary") or "")[:160],
            "assets_available": available,
            "assets_total": len(assets),
            "acknowledged": bool(payload.get("acknowledged")),
            "health": payload.get("account_health", {}),
        }

    def index(self, *, q: str = "", platform: str = "",
              limit: int = 100) -> Dict[str, Any]:
        """List published bundles (newest first) + clearly-labelled legacy .md."""
        entries = []
        for d in sorted(self._root.iterdir(), key=lambda p: p.name):
            if not d.is_dir() or d.name.startswith("."):
                continue
            e = self._entry(d.name)
            if e is None:
                continue
            ql = q.lower()
            if ql and ql not in (e["report_id"] + (e["summary"] or "")) \
                    and ql not in e["report_id"].lower():
                continue
            if platform and platform not in e["platforms"]:
                continue
            entries.append(e)
        entries.sort(key=lambda e: float(e["created_at"] or 0), reverse=True)
        return {"reports": entries[:int(limit)],
                "legacy": self.list_legacy()}

    def detail(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Safe detail payload: bounded, escaped-ready fields only; never a
        filesystem path, secret, cookie, token, or raw body."""
        d = self.bundle_dir(report_id)
        mf = d / "manifest.json"
        if not mf.exists():
            return None
        try:
            payload = json.loads(mf.read_text())
        except Exception:
            return None
        payload = dict(payload)
        payload["report_id"] = report_id
        payload["acknowledged"] = bool(payload.get("acknowledged"))
        payload["_has_bundle"] = True
        return payload

    def asset(self, report_id: str, asset_id: str
              ) -> Optional[Tuple[bytes, str]]:
        """Return (bytes, mime) for a bundle asset, refusing traversal and
        symlink escapes. Missing/mismatched/oversized assets return None (typed
        missing-evidence is handled by the caller)."""
        rid = self.validate_report_id(report_id)
        aid = self.validate_asset_id(asset_id)
        report_dir = self._bundle_realpath(rid)
        if report_dir is None:
            return None
        candidate = report_dir / "assets" / f"{aid}.webp"
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve()
        assets_dir = report_dir / "assets"
        if str(resolved).startswith(str(assets_dir) + os.sep) \
                and resolved.is_file():
            try:
                if resolved.stat().st_size > MAX_ASSET_BYTES:
                    return None   # oversized asset refused at serve time
                data = resolved.read_bytes()
            except OSError:
                return None
            digest = hashlib.sha256(data).hexdigest()
            mf = report_dir / "manifest.json"
            try:
                payload = json.loads(mf.read_text())
                declared = next((a for a in payload.get("assets", [])
                                 if a.get("asset_id") == aid), None)
                if declared is not None and declared.get("sha256") \
                        and digest != declared["sha256"]:
                    return None
                if declared is not None and declared.get("byte_count") \
                        and len(data) != declared["byte_count"]:
                    return None
            except Exception:
                return None
            return data, "image/webp"
        return None

    def acknowledge(self, report_id: str) -> bool:
        """Mark a report acknowledged (atomic marker; the canonical manifest
        keeps the flag)."""
        d = self.bundle_dir(report_id)
        mf = d / "manifest.json"
        if not mf.exists():
            return False
        try:
            payload = json.loads(mf.read_text())
        except Exception:
            return False
        payload["acknowledged"] = True
        _atomic_write_text(mf, json.dumps(payload, indent=2, ensure_ascii=False))
        try:
            os.chmod(mf, 0o600)
        except OSError:
            pass
        return True

    def export(self, report_id: str) -> Optional[bytes]:
        """Zip the report bundle into a single private blob."""
        d = self.bundle_dir(report_id)
        if not (d / "manifest.json").exists():
            return None
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(self._root))
        return buf.getvalue()

    def retention(self, *, dry_run: bool = True, retention_days: int = 90,
                  now: Optional[float] = None,
                  backup_dir: Optional[Path | str] = None) -> Dict[str, Any]:
        """Prune only EXPIRED report bundles, FAIL-CLOSED.

        Deletion happens ONLY after (1) every candidate is backed up into a
        fresh protected backup directory and verified, and (2) the report index
        integrity check passes. If backup or index validation is unavailable or
        fails, the result is typed BLOCKED/DEGRADED and NOTHING is deleted.
        Real paths are validated (report ID + realpath inside the root, no
        symlink escape) before each deletion; `ignore_errors` is never used.
        Legacy .md files, cursors, memory, evidence, unrelated reports, and user
        data are never touched. Every run records redacted report IDs, timestamps,
        policy, backup ID, index-check result, deleted count, and failures.
        """
        cutoff = (time.time() if now is None else float(now)) \
            - int(retention_days) * 86400
        candidates = []
        for e in self.index(limit=10000)["reports"]:
            created = float(e.get("created_at") or 0)
            if 0 < created < cutoff:
                candidates.append(e)
        result: Dict[str, Any] = {
            "state": "OK",
            "dry_run": bool(dry_run),
            "retention_days": int(retention_days),
            "cutoff": cutoff,
            "candidates": [{"report_id": e["report_id"],
                            "created_at": e["created_at"]} for e in candidates],
            "backup_id": None,
            "index_check": "not_run",
            "deleted": [],
            "failures": [],
        }
        if dry_run:
            return result
        # ---- fail-closed: protected backup + index integrity BEFORE deletion
        backup_id = f"retention-{uuid.uuid4().hex[:12]}"
        bdir = Path(backup_dir) if backup_dir else \
            self._root / f".backup-{backup_id}"
        try:
            self._backup_bundles([e["report_id"] for e in candidates], bdir)
            result["backup_id"] = backup_id
        except Exception as exc:
            result["state"] = "BLOCKED"
            result["failures"].append(
                f"backup failed: {redact_text(str(exc)[:200])}")
            self._log_retention(result)
            return result
        try:
            ok = self._index_integrity_ok()
        except Exception as exc:
            ok = False
            result["failures"].append(
                f"index check error: {redact_text(str(exc)[:200])}")
        result["index_check"] = "ok" if ok else "failed"
        if not ok:
            result["state"] = "BLOCKED"
            result["failures"].append("index integrity failed; nothing deleted")
            self._log_retention(result)
            return result
        # ---- validated per-report deletion (never ignore_errors)
        for e in candidates:
            rid = e["report_id"]
            try:
                target = self._bundle_realpath(rid)
                if target is None:
                    raise ReportStoreError("unsafe/symlink path refused")
                shutil.rmtree(target)
                result["deleted"].append(rid)
            except Exception as exc:
                result["failures"].append(
                    f"delete failed for {rid}: {redact_text(str(exc)[:160])}")
                result["state"] = "FAILED"
                break   # fail closed after a deletion failure
        if result["failures"] and result["state"] not in ("BLOCKED", "FAILED"):
            result["state"] = "DEGRADED"
        self._log_retention(result)
        return result

    def _backup_bundles(self, report_ids: List[str], bdir: Path) -> str:
        """Copy candidate bundles to a FRESH backup dir and verify each. The
        backup root is made 0700 IMMEDIATELY after creation (never left at a
        permissive umask mode like 0775)."""
        if bdir.exists():
            raise ReportStoreError("backup dir already exists")
        bdir.mkdir(parents=True)
        try:
            os.chmod(bdir, 0o700)
        except OSError as exc:
            raise ReportStoreError(
                f"backup dir not private (chmod 0700 failed): {exc}") from exc
        for rid in report_ids:
            src = self._bundle_realpath(rid)
            if src is None:
                raise ReportStoreError(f"unsafe path for {rid}")
            dst = bdir / rid
            shutil.copytree(src, dst, symlinks=False)
            mf = dst / "manifest.json"
            if not mf.exists():
                raise ReportStoreError(f"backup missing manifest for {rid}")
            json.loads(mf.read_text())
            _chmod_tree(dst)
        return str(bdir)

    def _bundle_realpath(self, report_id: str) -> Optional[Path]:
        """Resolve a bundle path strictly inside the report root (no symlink
        escape, no ``..``). Returns None when unsafe."""
        try:
            rid = self.validate_report_id(report_id)
        except ReportStoreError:
            return None
        candidate = self._root / rid
        if candidate.is_symlink():
            return None
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        root = self._root.resolve()
        if resolved != candidate:
            return None
        if not str(resolved).startswith(str(root) + os.sep):
            return None
        if not resolved.is_dir():
            return None
        return resolved

    def _index_integrity_ok(self) -> bool:
        """Every bundle manifest parses, ids are strict, no leftover stages,
        and the root is not a symlink."""
        if self._root.is_symlink():
            return False
        for d in self._root.iterdir():
            if d.is_symlink():
                return False
            if d.is_dir():
                if d.name.startswith("."):
                    if d.name.startswith(".stage-"):
                        return False   # stale stage = interrupted write
                    continue
                if not REPORT_ID_RE.match(d.name):
                    return False
                mf = d / "manifest.json"
                if not mf.exists():
                    return False
                try:
                    json.loads(mf.read_text())
                except Exception:
                    return False
        return True

    def _log_retention(self, result: Dict[str, Any]) -> None:
        try:
            log = self._root / "retention.log"
            record = {
                "ts": time.time(),
                "state": result.get("state"),
                "dry_run": result.get("dry_run"),
                "retention_days": result.get("retention_days"),
                "backup_id": result.get("backup_id"),
                "index_check": result.get("index_check"),
                "deleted_count": len(result.get("deleted", [])),
                "deleted": result.get("deleted", []),
                "failures": result.get("failures", []),
            }
            with open(log, "a") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            try:
                os.chmod(log, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def list_legacy(self) -> List[Dict[str, Any]]:
        """Index legacy root-level Markdown as clearly labelled legacy data.
        Never deletes or modifies it."""
        out = []
        for p in sorted(self._root.iterdir()):
            if p.is_file() and p.suffix == ".md" and LEGACY_MD_RE.match(p.name):
                try:
                    st = p.stat()
                except OSError:
                    continue
                out.append({"name": p.name, "bytes": st.st_size,
                            "modified": st.st_mtime, "legacy": True})
        return out

    def regen(self, store: CommunicationsStore, report_id: str,
              ) -> Optional[Dict[str, Any]]:
        """Re-render a committed report from the canonical 8A store (rollback /
        recovery path). Idempotent; preserves previous bundle as a backup."""
        data = self._reconstruct(store, report_id)
        if data is None:
            return None
        manifest, items, assets, health = data
        asset_bytes = store.asset_bytes_for_report(report_id)
        return self.render(manifest, items, assets, asset_bytes=asset_bytes,
                           account_health=health, keep_backup=True)

    def render_from_store(self, store: CommunicationsStore, report_id: str,
                          ) -> Optional[Dict[str, Any]]:
        return self.regen(store, report_id)

    # --------------------------------------------------------- reconstruction
    @staticmethod
    def _reconstruct(store: CommunicationsStore, report_id: str
                     ) -> Optional[Tuple[ReportManifest, List[CommunicationItem],
                                         List[EvidenceAssetRecord],
                                         Dict[str, Any]]]:
        conn = store._conn
        mrow = conn.execute(
            "SELECT * FROM report_manifest WHERE report_id=?",
            (report_id,)).fetchone()
        if mrow is None:
            return None
        item_rows = conn.execute(
            "SELECT * FROM item WHERE report_id=?", (report_id,)).fetchall()
        asset_rows = conn.execute(
            "SELECT * FROM evidence_asset WHERE report_id=?",
            (report_id,)).fetchall()
        scan_rows = conn.execute(
            "SELECT platform, account_scope FROM scan WHERE report_id=?",
            (report_id,)).fetchall()
        health: Dict[str, Any] = {}
        for pl, acct in {(r["platform"], r["account_scope"]) for r in scan_rows}:
            try:
                h = store.latest_health(Platform(pl), acct)
            except Exception:
                h = None
            if h is not None:
                health[acct] = f"{h.status.value}" + \
                    (f": {h.reason}" if h.reason else "")
        item_rows = conn.execute(
            "SELECT * FROM item WHERE report_id=?", (report_id,)).fetchall()
        platforms = sorted({r["platform"] for r in scan_rows}) or \
            sorted({r["platform"] for r in item_rows})
        accounts = sorted({r["account_scope"] for r in scan_rows}) or \
            sorted({r["account_scope"] for r in item_rows})
        manifest = ReportManifest.create(
            report_id=report_id,
            window_start=float(mrow["window_start"]),
            window_end=float(mrow["window_end"]),
            status=ReportStatus(mrow["status"]),
            new_count=int(mrow["new_count"]),
            created_at=float(mrow["created_at"]),
            generated_analysis=bool(mrow["generated_analysis"]),
            analysis_disclaimer=mrow["analysis_disclaimer"] or "",
            retention_days=int(mrow["retention_days"]),
            summary=mrow["summary"] or "",
            platforms=tuple(platforms), accounts=tuple(accounts),
            item_identities=tuple(r["identity"] for r in item_rows),
            assets=tuple(a["asset_id"] for a in asset_rows),
            partial_failures=())
        items = [
            CommunicationItem(
                platform=Platform(r["platform"]),
                account_scope=r["account_scope"],
                identity=r["identity"],
                identity_source=IdentitySource(r["identity_source"]),
                identity_version=int(r["identity_version"]),
                collision_key=r["collision_key"],
                sender=r["sender"] or "", display_name=r["display_name"] or "",
                ts_normalized=r["ts_normalized"] or "",
                ts_raw=r["ts_raw"] or "", subject=r["subject"] or "",
                preview=r["preview"] or "", is_new=bool(r["is_new"]),
                read_state=ReadState(r["read_state"]), safe_url=r["safe_url"] or "",
                captured_at=float(r["captured_at"]),
                content_hash=r["content_hash"] or "",
                adapter=r["adapter"] or "", adapter_version=r["adapter_version"] or "",
                evidence_status=EvidenceStatus(r["evidence_status"]),
                sensitivity=Sensitivity(r["sensitivity"]),
                redacted=bool(r["redacted"]),
                source_item_id=r["source_item_id"] or "",
                thread_id=r["thread_id"] or "")
            for r in item_rows]
        assets = [
            EvidenceAssetRecord(
                asset_id=a["asset_id"], report_id=a["report_id"],
                item_identity=a["item_identity"] or "",
                mime=a["mime"] or "", byte_count=int(a["byte_count"]),
                sha256=a["sha256"] or "",
                captured_at=float(a["captured_at"]),
                redaction_status=EvidenceStatus(a["redaction_status"]),
                source_item_id=a["source_item_id"] or "",
                width=int(a["width"]), height=int(a["height"]))
            for a in asset_rows]
        return manifest, items, assets, health

    # ---------------------------------------------------------------- content
    def render_markdown(self, payload: Dict[str, Any]) -> str:
        lines = [f"# Report {payload['report_id']}",
                 "",
                 f"- **status**: {payload['status'].upper()}",
                 f"- **window**: {_fmt_ts(payload['window_start'])} → "
                 f"{_fmt_ts(payload['window_end'])}",
                 f"- **created**: {_fmt_ts(payload['created_at'])}",
                 f"- **new items**: {payload['new_count']}",
                 f"- **accounts**: {', '.join(payload['accounts']) or '—'}",
                 ""]
        if payload.get("summary"):
            lines += ["## Executive summary", "",
                      _md_escape(payload["summary"]), ""]
        if payload.get("generated_analysis"):
            lines += [f"> {_md_escape(payload.get('analysis_disclaimer', ''))}",
                      ""]
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for it in payload["items"]:
            groups.setdefault((it["platform"], it["account_scope"]), []).append(it)
        if groups:
            lines.append("## Items")
            lines.append("")
            for (platform, account), items in sorted(groups.items()):
                lines.append(f"### {platform} / {account}")
                for it in items:
                    unread = {"unread": "UNREAD", "read": "read",
                              "unknown": "unknown"}.get(it["read_state"], "")
                    src = it.get("safe_url") or "unsafe source"
                    lines.append(
                        f"- **{_md_escape(it['sender'])}** — "
                        f"{_md_escape(it['subject'] or it['preview'])} — "
                        f"{_md_escape(it['ts_raw'] or _fmt_ts(it['captured_at']))}"
                        f"{f' [{unread}]' if unread else ''} — "
                        f"evidence:{it['evidence_status']} — "
                        f"source: {_md_escape(src)}")
                lines.append("")
        if payload.get("account_health"):
            lines.append("## Account health")
            for scope, h in sorted(payload["account_health"].items()):
                lines.append(f"- {_md_escape(scope)}: {_md_escape(str(h))}")
            lines.append("")
        if payload.get("assets"):
            lines.append("## Evidence")
            for a in payload["assets"]:
                state = "available" if a["redaction_status"] == "available" \
                    else a["redaction_status"]
                lines.append(f"- asset {a['asset_id']}: {state} "
                             f"(sha256 {a['sha256'][:12]}…)")
            lines.append("")
        if payload.get("partial_failures"):
            lines.append("## Partial failures")
            for pf in payload["partial_failures"]:
                lines.append(f"- {_md_escape(pf)}")
            lines.append("")
        lines += ["---", "", "_Private local report. No cookies, tokens, or "
                  "credentials are included._", ""]
        return "\n".join(lines)

    def render_html(self, payload: Dict[str, Any]) -> str:
        rows: List[str] = []
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for it in payload["items"]:
            groups.setdefault((it["platform"], it["account_scope"]), []).append(it)
        assets_by_item = {a["item_identity"]: a for a in payload["assets"]}
        for (platform, account), items in sorted(groups.items()):
            rows.append(f'<h3>{html.escape(platform)} / '
                        f'{html.escape(account)}</h3>')
            for it in items:
                link = _link_html(it.get("safe_url"))
                unread = {"unread": "unread", "read": "read",
                          "unknown": "unknown"}[it["read_state"]]
                thumb = _asset_html(assets_by_item.get(it["identity"]))
                rows.append(
                    f'<li class="item"><div class="item-head">'
                    f'<span class="sender">{html.escape(it["sender"])}</span>'
                    f'<span class="meta">[{unread}] · '
                    f'{html.escape(it["ts_raw"] or _fmt_ts(it["captured_at"]))}'
                    f'</span></div>'
                    f'<div class="body">{html.escape(it["subject"] or "")} '
                    f'{html.escape(it["preview"] or "")}</div>'
                    f'<div class="foot">evidence: '
                    f'{html.escape(it["evidence_status"])} · {link}'
                    f'</div>{thumb}</li>')
        items_html = "\n".join(rows) if rows else \
            '<p class="empty">No items in this report window.</p>'
        disclaimer_html = ""
        if payload.get("generated_analysis"):
            disclaimer_html = (
                '<p class="meta">Generated analysis: '
                + html.escape(payload.get("analysis_disclaimer", "")) + '</p>')
        return _HTML_TEMPLATE.format(
            title=html.escape(f"Report {payload['report_id']}"),
            status=html.escape(payload["status"].upper()),
            report_id=html.escape(payload["report_id"]),
            window=(html.escape(_fmt_ts(payload["window_start"])) + " → " +
                    html.escape(_fmt_ts(payload["window_end"]))),
            created=html.escape(_fmt_ts(payload["created_at"])),
            new_count=int(payload["new_count"]),
            accounts=html.escape(", ".join(payload["accounts"]) or "—"),
            summary=html.escape(payload.get("summary", "")) or "No summary.",
            disclaimer_html=disclaimer_html,
            items=items_html,
            health=_health_html(payload.get("account_health")),
            partial=_partial_html(payload.get("partial_failures")),
            assets=_assets_list_html(payload.get("assets")))


def _link_html(url: Optional[str]) -> str:
    if url:
        return (f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer" class="src">open source</a>')
    return '<span class="unsafe" title="unsafe or unavailable source">' \
           'unsafe source</span>'


def _asset_html(asset: Optional[Dict[str, Any]]) -> str:
    if not asset:
        return '<div class="ev miss">evidence not available</div>'
    if asset["redaction_status"] == "available":
        return (f'<a class="thumb" href="assets/'
                f'{html.escape(asset["asset_id"], quote=True)}.webp" '
                f'target="_blank"><img alt="scoped screenshot thumbnail" '
                f'src="assets/{html.escape(asset["asset_id"], quote=True)}.webp">'
                f'</a>')
    return f'<div class="ev miss">evidence ' \
        f'{html.escape(asset["redaction_status"])}</div>'


def _health_html(health: Optional[Dict[str, Any]]) -> str:
    if not health:
        return '<p class="empty">No health records.</p>'
    rows = [f'<li><span>{html.escape(str(k))}</span>: '
            f'{html.escape(str(v))}</li>' for k, v in health.items()]
    return '<ul>' + "".join(rows) + '</ul>'


def _partial_html(partial: Optional[List[str]]) -> str:
    if not partial:
        return ""
    rows = [f'<li>{html.escape(str(p))}</li>' for p in partial]
    return '<h3>Partial failures</h3><ul>' + "".join(rows) + '</ul>'


def _assets_list_html(assets: Optional[List[Dict[str, Any]]]) -> str:
    if not assets:
        return '<p class="empty">No evidence assets.</p>'
    rows = [f'<li>{html.escape(a["asset_id"])}: '
            f'{html.escape(a["redaction_status"])} · '
            f'<code>{html.escape(a["sha256"][:12])}…</code></li>'
            for a in assets]
    return '<ul>' + "".join(rows) + '</ul>'


# ---------------------------------------------------------------------------
# OC-D01: Team Reports aggregator (GitHub / Discord activity)
# ---------------------------------------------------------------------------
# The upstream team-reports plugin aggregates GitHub + explicitly configured
# Discord activity into a stored, optionally summarized history. Qubit adds the
# local read model here: it validates sources, calls an *injected* provider
# transport (the external credential/network boundary), normalizes bounded
# activity rows, and builds a deterministic summary. Without a transport the
# call is a typed TEAM_REPORTS_UNAVAILABLE -- never a fabricated report.
TEAM_REPORTS_SCHEMA = "qubit-team-reports/1"
TEAM_REPORTS_UNAVAILABLE = "TEAM_REPORTS_UNAVAILABLE"
TEAM_REPORTS_BAD_SOURCE = "TEAM_REPORTS_BAD_SOURCE"
MAX_TEAM_SOURCES = 16
MAX_TEAM_ITEMS = 500
_TEAM_SOURCE_PLATFORMS = ("github", "discord")


def _check_team_sources(sources: Any) -> List[Dict[str, str]]:
    if not isinstance(sources, (list, tuple)) or not sources:
        raise ReportValidationError("at least one team-report source required")
    if len(sources) > MAX_TEAM_SOURCES:
        raise ReportValidationError(
            f"at most {MAX_TEAM_SOURCES} sources per report")
    clean: List[Dict[str, str]] = []
    for src in sources:
        if not isinstance(src, dict):
            raise ReportValidationError("each source must be a mapping")
        platform = str(src.get("platform", "")).strip().lower()
        if platform not in _TEAM_SOURCE_PLATFORMS:
            raise ReportValidationError(
                f"source platform must be one of {_TEAM_SOURCE_PLATFORMS}")
        account = str(src.get("account", "")).strip()[:120]
        target = str(src.get("target", "")).strip()[:200]
        if not account:
            raise ReportValidationError("source account is required")
        clean.append({"platform": platform, "account": account,
                      "target": target})
    return clean


def team_reports(cfg: Any, sources: Any, *, transport: Any = None,
                 window_start: Optional[float] = None,
                 window_end: Optional[float] = None,
                 retention_days: int = 90,
                 requested_by: str = "cli:user") -> Dict[str, Any]:
    """Aggregate configured GitHub/Discord activity into a team report.

    ``transport`` is the injected provider boundary: a callable
    ``(request) -> {"items": [...]}``. The aggregator itself (validation,
    bounds, redaction, deterministic summary) is real and tested with a mocked
    transport; the live API call is the credential-gated last mile.
    """
    try:
        clean_sources = _check_team_sources(sources)
    except ReportValidationError as exc:
        return {"state": "FAILED", "code": TEAM_REPORTS_BAD_SOURCE,
                "schema": TEAM_REPORTS_SCHEMA, "detail": str(exc),
                "owner": "qubit-policy",
                "rollback": "nothing aggregated; nothing to roll back"}
    if transport is None:
        return {"state": "UNAVAILABLE", "code": TEAM_REPORTS_UNAVAILABLE,
                "schema": TEAM_REPORTS_SCHEMA,
                "detail": "team reports need a GitHub/Discord transport; "
                          "inject one or run `qubit monitor` for local "
                          "communications evidence",
                "owner": "qubit-policy",
                "rollback": "nothing aggregated; nothing to roll back"}
    now = time.time()
    start = float(window_start) if window_start is not None else now - 86400.0
    end = float(window_end) if window_end is not None else now
    if end < start:
        return {"state": "FAILED", "code": TEAM_REPORTS_BAD_SOURCE,
                "schema": TEAM_REPORTS_SCHEMA,
                "detail": "window_end precedes window_start",
                "owner": "qubit-policy",
                "rollback": "nothing aggregated; nothing to roll back"}
    rows: List[Dict[str, Any]] = []
    failures: List[str] = []
    for src in clean_sources:
        request = {"kind": "team.activity", "platform": src["platform"],
                   "account": src["account"], "target": src["target"],
                   "window_start": start, "window_end": end,
                   "limit": MAX_TEAM_ITEMS}
        try:
            response = transport(request)
        except Exception as exc:
            failures.append(f"{src['platform']}:{redact_text(str(exc))[:120]}")
            continue
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            failures.append(f"{src['platform']}: no items returned")
            continue
        for item in items[:MAX_TEAM_ITEMS]:
            if not isinstance(item, dict):
                continue
            rows.append({
                "platform": src["platform"], "account": src["account"],
                "actor": redact_text(str(item.get("actor", "")))[:120],
                "summary": redact_text(str(item.get("summary", "")))[:240],
                "ts": item.get("ts"),
                "url": safe_source_link(str(item.get("url", ""))) or "",
                "channel": redact_text(str(item.get("channel", "")))[:120],
            })
    by_platform: Dict[str, int] = {}
    for row in rows:
        by_platform[row["platform"]] = by_platform.get(row["platform"], 0) + 1
    return {
        "state": "SUCCESS" if rows and not failures else (
            "PARTIAL" if rows else "DEGRADED"),
        "schema": TEAM_REPORTS_SCHEMA,
        "window_start": start, "window_end": end,
        "retention_days": int(retention_days),
        "counts": {"total": len(rows), "by_platform": by_platform},
        "items": sorted(rows, key=lambda r: r.get("ts") or 0,
                        reverse=True)[:MAX_TEAM_ITEMS],
        "partial_failures": failures,
        "requested_by": str(requested_by)[:64],
        "authority": "injected provider transport; bounded redacted rows",
    }


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font:16px/1.5 system-ui,sans-serif;
          background:#0d1117; color:#e6edf3; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:16px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:16px; margin:20px 0 6px; border-bottom:1px solid #21262d; }}
  h3 {{ font-size:14px; margin:14px 0 4px; }}
  .status {{ display:inline-block; padding:2px 10px; border-radius:12px;
             font-size:12px; }}
  .status.SUCCESS {{ background:#238636; color:#fff; }}
  .status.PARTIAL {{ background:#9e6a03; color:#fff; }}
  .status.DEGRADED {{ background:#da3633; color:#fff; }}
  .meta {{ color:#8b949e; font-size:13px; }}
  .summary {{ background:#161b22; border:1px solid #30363d; border-radius:8px;
              padding:10px; }}
  ul.items {{ list-style:none; padding:0; }}
  li.item {{ background:#161b22; border:1px solid #30363d; border-radius:8px;
             padding:10px; margin:8px 0; }}
  .item-head {{ display:flex; justify-content:space-between; gap:8px; }}
  .sender {{ font-weight:600; }}
  .body {{ color:#c9d1d9; margin:4px 0; }}
  .foot {{ color:#8b949e; font-size:13px; }}
  a {{ color:#58a6ff; text-decoration:none; }}
  a.thumb img {{ max-width:240px; border-radius:6px; margin-top:6px;
                 display:block; }}
  .unsafe, .ev.miss {{ color:#f85149; font-size:12px; }}
  .empty {{ color:#8b949e; }}
  @media (max-width:600px) {{
    body {{ font-size:15px; }}
    .item-head {{ flex-direction:column; }}
    a.thumb img {{ max-width:100%; }}
  }}
  @media print {{
    body {{ background:#fff; color:#000; }}
    .wrap {{ max-width:none; }}
    li.item {{ border:1px solid #ccc; }}
    a {{ color:#000; }}
  }}
</style>
</head><body>
<div class="wrap">
  <h1>{title}</h1>
  <span class="status {status}">{status}</span>
  <div class="meta">window {window} · created {created} ·
    new items {new_count} · accounts {accounts}</div>
  <h2>Executive summary</h2>
  <div class="summary">{summary}</div>
  {disclaimer_html}
  <h2>Items</h2>
  <ul class="items">{items}</ul>
  <h2>Account health</h2>
  {health}
  {partial}
  <h2>Evidence</h2>
  {assets}
  <p class="meta" style="margin-top:16px">Private local report. No cookies,
    tokens, or credentials are included.</p>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Index / detail / asset / ack / export / retention / legacy / reconstruction
# ---------------------------------------------------------------------------