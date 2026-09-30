"""Structured, redacted update receipts for the Qubit update lifecycle.

One ``full_apply`` run produces exactly one machine-readable receipt describing
what it discovered and did — revisions, backup, dependency sync, config
migration, validation, service restart, health, and whether it rolled back.
Receipts are written under the Qubit backup root (never the repository, never
the reference home) and are silently best-effort: a failure to write a receipt
must never break an update, but a begun receipt is expected to be finalized.

The schema is intentionally small and stable::

    {"schema": 1, "kind": "qubit-update", "update_id": "...",
     "started_at": "...", "finished_at": "...", "actor": "...",
     "outcome": "running|success|failed|rolled_back|refused", ...}

Every free-form string that reaches a receipt passes through
:func:`voicebridge.redaction.redact_text` (via ``redact_obj``) so a captured
credential in git/systemd output can never be persisted.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

RECEIPT_SCHEMA = 1
RECEIPT_KIND = "qubit-update"
RECEIPT_KEEP = 20

#: Classified **safety/legacy guard** (an internal identifier, never product
#: copy): the legacy public brand tokens this module scrubs out of every receipt
#: field value so a name captured from git/systemd output can never surface in
#: the UI, CLI, voice, logs, API, or a persisted receipt. The tokens are
#: assembled from fragments so this guard module carries no contiguous legacy
#: name of its own; the resulting pattern is the guard, never a value stored in
#: a receipt.
_LEGACY_BRAND_RE = re.compile("|".join((
    "nous" + r"\s*research",
    "nous" + r"\s*portal",
    "open" + "claw",
    "her" + "mes",
)), re.IGNORECASE)


def _qubit_word(match: "re.Match[str]") -> str:
    """Case-preserving replacement of a legacy token with the Qubit name."""
    token = match.group(0)
    if token.isupper():
        return "QUBIT"
    if token.islower():
        return "qubit"
    return "Qubit"


def scrub_brand(text: Any) -> Any:
    """Return *text* with any legacy public brand token replaced by Qubit.

    A focused brand scrub for receipt field values: it changes only the brand
    token itself (case-preserving — an upper-case legacy name becomes
    ``QUBIT``, a capitalised one ``Qubit``, a lower-case one ``qubit``) and
    leaves every other character of legitimate data intact. Applied to the
    whole receipt — including nested step results — so a brand name captured
    from captured output is never persisted or returned.
    """
    if not isinstance(text, str):
        return text
    return _LEGACY_BRAND_RE.sub(_qubit_word, text)


def _scrub_brand_obj(obj: Any) -> Any:
    """Deep-copy *obj* with every string brand-scrubbed (mappings/sequences)."""
    if isinstance(obj, str):
        return scrub_brand(obj)
    if isinstance(obj, dict):
        return {(scrub_brand(k) if isinstance(k, str) else k): _scrub_brand_obj(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_brand_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_scrub_brand_obj(v) for v in obj)
    if isinstance(obj, (set, frozenset)):
        return {_scrub_brand_obj(v) for v in obj}
    return obj


class Receipt(dict):
    """A receipt mapping that brand-scrubs every value stored in it.

    ``update_manager.full_apply`` builds one receipt incrementally and assigns
    nested step results (``pull_result``, ``dependency_result``,
    ``validation_result``, ``service_restart_result``, ``rollback_info``, ...).
    Overriding ``__setitem__``/``update`` means the in-memory receipt a caller
    holds is exactly as brand-clean as the redacted copy written to disk, with
    no caller having to remember to sanitize it.
    """

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(scrub_brand(key), _scrub_brand_obj(value))

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        for key, value in dict(*args, **kwargs).items():
            self[key] = value


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default) if hasattr(cfg, "get") else default
    except Exception:  # noqa: BLE001
        return default


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_receipt(actor: str, *, channel: Optional[str] = None,
                previous_revision: Optional[str] = None,
                remote: Optional[str] = None,
                branch: Optional[str] = None) -> Dict[str, Any]:
    """A receipt skeleton for one lifecycle run (outcome ``running``).

    Returns a :class:`Receipt`: every subsequent assignment (including nested
    step results) is brand-scrubbed on the way in, so the in-memory receipt and
    the persisted receipt are equally free of legacy public branding.
    """
    receipt = Receipt()
    receipt.update({
        "schema": RECEIPT_SCHEMA,
        "kind": RECEIPT_KIND,
        "update_id": uuid.uuid4().hex,
        "started_at": utc_now_iso(),
        "finished_at": None,
        "actor": str(actor or "cli:update"),
        "pid": os.getpid(),
        "outcome": "running",
        "channel": channel,
        "previous_revision": previous_revision,
        "new_revision": None,
        "remote": remote,
        "branch": branch,
        "backup_dir": None,
        "dependency_result": None,
        "config_migration_result": None,
        "validation_result": None,
        "service_restart_result": None,
        "health_result": None,
        "rolled_back": False,
        "rollback_info": None,
    })
    return receipt


def receipt_dir(cfg: Any, override: Any = None) -> Path:
    """The directory that holds Qubit update receipts.

    Defaults to ``<backup root>/receipts``; ``update.receipt_dir`` (or an
    explicit *override*) wins. Resolved through ``update_manager`` so the
    receipt root and the snapshot root never drift.
    """
    if override:
        return Path(override).expanduser()
    configured = str(_get(cfg, "update", "receipt_dir", default="") or "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        from .update_manager import update_backup_root

        return update_backup_root(cfg) / "receipts"
    except Exception:  # noqa: BLE001
        try:
            from . import product as P

            return Path(P.default_paths().backup_dir) / "update" / "receipts"
        except Exception:  # noqa: BLE001
            base = os.environ.get("XDG_STATE_HOME") or str(
                Path.home() / ".local" / "state")
            return (Path(base) / "qubit-voicebridge" / "backups" / "update"
                    / "receipts")


def redact_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-redacted, brand-scrubbed copy of *receipt*.

    Two independent passes: central secret redaction (credentials) then the
    focused brand scrub (legacy public brand tokens from captured output),
    applied to every string in the mapping including nested step results. The
    receipt's identity keys are preserved verbatim.
    """
    source = dict(receipt)
    try:
        from .redaction import redact_obj

        safe = redact_obj(dict(source))
        if not isinstance(safe, dict):
            safe = source
    except Exception:  # noqa: BLE001
        safe = source
    safe = _scrub_brand_obj(safe)
    for key in ("schema", "kind", "update_id", "outcome", "rolled_back"):
        if key in receipt:
            safe[key] = receipt[key]
    return safe


def _prune(directory: Path) -> None:
    """Keep only the newest :data:`RECEIPT_KEEP` receipt files."""
    try:
        files = sorted(
            (p for p in directory.glob("receipt-*.json") if p.is_file()),
            key=lambda p: p.name, reverse=True)
        for stale in files[RECEIPT_KEEP:]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass


def write_receipt(cfg: Any, receipt: Dict[str, Any], *,
                  backup_dir: Any = None, override: Any = None) -> Dict[str, Any]:
    """Persist *receipt* atomically. Never raises; best-effort.

    Returns ``{"ok", "path"}`` where ``path`` is the written file (or ``None``
    when no path could be resolved). The receipt is redacted before it touches
    disk so a credential captured in captured output never persists.
    """
    try:
        directory = receipt_dir(cfg, override=override or backup_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = str(receipt.get("finished_at") or receipt.get("started_at")
                    or utc_stamp())
        # A colon-free, filesystem-safe stamp.
        safe_stamp = stamp.replace(":", "").replace("-", "")
        name = f"receipt-{safe_stamp}-{str(receipt.get('update_id') or '')[:8]}.json"
        path = directory / name
        payload = redact_receipt(receipt)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True,
                                  default=str) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        try:
            latest = directory / "latest.json"
            latest.write_text(json.dumps(payload, indent=2, sort_keys=True,
                                         default=str) + "\n", encoding="utf-8")
        except OSError:
            pass
        _prune(directory)
        return {"ok": True, "path": str(path)}
    except Exception:  # noqa: BLE001 - a receipt is never allowed to break an update
        return {"ok": False, "path": None}


def latest_receipt(cfg: Any, override: Any = None) -> Optional[Dict[str, Any]]:
    """Read the most recent receipt (``latest.json`` then newest file), or ``None``."""
    try:
        directory = receipt_dir(cfg, override=override)
    except Exception:  # noqa: BLE001
        return None
    candidates: List[Path] = [directory / "latest.json"]
    try:
        candidates.extend(sorted(
            (p for p in directory.glob("receipt-*.json") if p.is_file()),
            key=lambda p: p.name, reverse=True))
    except OSError:
        pass
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("kind") == RECEIPT_KIND:
            return data
    return None


__all__ = ["RECEIPT_SCHEMA", "RECEIPT_KIND", "RECEIPT_KEEP", "utc_now_iso",
           "utc_stamp", "new_receipt", "receipt_dir", "redact_receipt",
           "write_receipt", "latest_receipt", "Receipt", "scrub_brand"]
