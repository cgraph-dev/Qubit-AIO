"""Licence-aware asset build for Qubit Office (P6-06).

Every asset that could reach a room, a character, a panel, a map or an effect
must carry provenance: which pack it came from, its sha256, the licence (or the
recorded licence file), and its intended use. A build **fails closed** when any
of those is missing, so an unlicensed sprite cannot be bundled by accident.

What this module guarantees
---------------------------
* **Provenance is required.** :func:`require_provenance` raises a typed
  :class:`AssetBuildError` when a row lacks pack/licence/hash/use.
* **Paid art is never redistributed.** A paid asset is metadata-only in the
  manifest; a paid asset that claims redistribution is a typed refusal.
* **A bundle manifest is licence-aware.** It records licence, licence source,
  paid/local-only state, credit, sha256 and use for every asset, plus stable
  counts and a digest.
* **Leakage is scanned, not assumed.** :func:`scan_for_leaks` walks a bundle,
  cache, screenshots, backups or export tree and reports paid pack names,
  archives/generator binaries, files whose sha256 matches paid asset hashes,
  and text files that reference the operator's paid-art root.

Nothing here copies, decodes or redistributes art. The only bytes read are a
sha256 of a candidate file during a leak scan. Pure Python; no clock, no
network, no store.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "SCHEMA",
    "LEAK_SCHEMA",
    "VERSION",
    "USES",
    "PAID_PACK_ALIASES",
    "LEAK_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "AssetBuildError",
    "AssetProvenance",
    "LeakFinding",
    "LeakReport",
    "require_provenance",
    "build_bundle",
    "validate_bundle",
    "scan_for_leaks",
    "scan_targets",
    "compute_digest",
]

SCHEMA = "qubit-office-asset-build/1"
LEAK_SCHEMA = "qubit-office-asset-leak-scan/1"
VERSION = 1

USES: Tuple[str, ...] = (
    "room", "style", "character", "portrait", "ui", "outside", "fx", "unknown",
)

#: Pack/name fragments that identify paid art. Matched case-insensitively after
#: normalising spaces and separators to underscores.
PAID_PACK_ALIASES: Tuple[str, ...] = (
    "modern_interiors",
    "modern_interiors_rpg_maker_version",
    "modernuserinterface",
    "modern_user_interface",
    "character_generator",
    "character_generator_2.0",
    "portrait_generator",
    "serene_village",
    "serene_village_revamped",
)

#: Extensions that are never allowed to leave the operator machine.
LEAK_EXTENSIONS: Tuple[str, ...] = (
    ".zip", ".7z", ".rar", ".exe", ".msi", ".psd", ".aseprite", ".gguf",
    ".onnx", ".safetensors",
)

#: Text-ish files that may leak a paid-art path in a manifest, log or export.
TEXT_EXTENSIONS: Tuple[str, ...] = (
    ".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".log", ".csv",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".css", ".toml", ".ini",
)

#: Operator-local roots that must never appear in a shipped text artifact.
FORBIDDEN_TEXT_MARKERS: Tuple[str, ...] = (
    "/desktop/ui office",
    "ui office/",
    "desktop/ui office",
)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_LICENSE_STATUS_OK = frozenset(
    {"license_file_present", "verified", "licensed", "mit", "cc0",
     "public_domain", "paid_local_only", "proprietary"})

_MAX_SCAN_FILES = 20000
_MAX_HASH_BYTES = 256 * 1024 * 1024
_MAX_TEXT_BYTES = 1024 * 1024


class AssetBuildError(ValueError):
    """A typed asset-build refusal. ``code`` is stable and testable."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ------------------------------------------------------------------ helpers
def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _strip_digest(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_digest(v) for k, v in value.items() if k != "digest"}
    if isinstance(value, (list, tuple)):
        return [_strip_digest(v) for v in value]
    return value


def compute_digest(payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 of a serialised manifest (``digest`` ignored)."""
    return hashlib.sha256(
        json.dumps(_strip_digest(payload), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_pack(value: str) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_")


# --------------------------------------------------------------- provenance
@dataclass(frozen=True)
class AssetProvenance:
    asset_id: str
    pack: str
    sha256: str
    use: str
    license: str
    license_source: str
    paid: bool
    local_only: bool
    redistribution_allowed: bool
    credit: str
    bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "pack": self.pack,
            "sha256": self.sha256,
            "use": self.use,
            "license": self.license,
            "license_source": self.license_source,
            "paid": bool(self.paid),
            "local_only": bool(self.local_only),
            "redistribution_allowed": bool(self.redistribution_allowed),
            "credit": self.credit,
            "bytes": int(self.bytes),
        }


def _license_of(row: Mapping[str, Any]) -> Tuple[str, str]:
    explicit = _text(row.get("license"))
    status = _text(row.get("license_status"))
    source = _text(row.get("license_source"))
    if explicit:
        return explicit, source or status
    if status and status.lower() in _LICENSE_STATUS_OK:
        return f"{status}: {source}" if source else status, source
    return "", source


def _provenance_row(row: Any, index: int) -> AssetProvenance:
    if not isinstance(row, Mapping):
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING", f"row {index} is not a mapping")
    asset_id = _text(row.get("asset_id") or row.get("path")
                     or row.get("relative_path"))
    if not asset_id:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING",
            f"row {index} has no asset_id/path")
    pack = _text(row.get("pack"))
    if not pack:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING", f"asset {asset_id!r} has no pack")
    sha = _text(row.get("sha256"))
    if not sha:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING", f"asset {asset_id!r} has no sha256")
    if not _SHA_RE.match(sha.lower()):
        raise AssetBuildError(
            "ASSET_HASH_INVALID",
            f"asset {asset_id!r} sha256 is not 64 lowercase hex")
    use = _text(row.get("intended_use") or row.get("use"))
    if not use:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING", f"asset {asset_id!r} has no use")
    if use not in USES:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING",
            f"asset {asset_id!r} use {use!r} is not one of {USES}")
    license_value, license_source = _license_of(row)
    if not license_value:
        raise AssetBuildError(
            "ASSET_LICENSE_UNVERIFIED",
            f"asset {asset_id!r} has no verified licence")
    paid = _as_bool(row.get("paid"), False)
    local_only = _as_bool(row.get("local_only", row.get("paid_local_only")),
                          paid)
    redistribution = _as_bool(
        row.get("redistribution_allowed"), not paid)
    credit = _text(row.get("credit"))
    if paid and _as_bool(row.get("credit_required"), True) and not credit:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING",
            f"paid asset {asset_id!r} has no required credit")
    if paid:
        if not local_only:
            raise AssetBuildError(
                "ASSET_PAID_REDISTRIBUTION",
                f"paid asset {asset_id!r} must stay local_only")
        if redistribution:
            raise AssetBuildError(
                "ASSET_PAID_REDISTRIBUTION",
                f"paid asset {asset_id!r} must not be redistributable")
    return AssetProvenance(
        asset_id=asset_id,
        pack=pack,
        sha256=sha.lower(),
        use=use,
        license=license_value,
        license_source=license_source,
        paid=paid,
        local_only=local_only,
        redistribution_allowed=redistribution,
        credit=credit,
        bytes=int(row.get("bytes") or 0),
    )


def require_provenance(rows: Iterable[Mapping[str, Any]]
                       ) -> List[AssetProvenance]:
    """Return normalized provenance rows, or raise a typed refusal."""
    records = [_provenance_row(row, index) for index, row in enumerate(rows)]
    if not records:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING", "the ledger declares no assets")
    ids = [record.asset_id for record in records]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise AssetBuildError(
            "ASSET_PROVENANCE_MISSING",
            f"duplicate asset ids {duplicates}")
    return records


# -------------------------------------------------------------- bundle build
def build_bundle(rows: Iterable[Mapping[str, Any]], *, bundle_id: str,
                 credit: str = "") -> Dict[str, Any]:
    """Build a licence-aware bundle manifest; paid art is metadata-only."""
    records = require_provenance(rows)
    assets: List[Dict[str, Any]] = []
    for record in records:
        entry = record.to_dict()
        entry["included_in_bundle"] = not record.paid
        entry["status"] = "metadata-only" if record.paid else "bundled"
        if record.paid and not entry["credit"]:
            entry["credit"] = credit
        assets.append(entry)
    included = [a for a in assets if a["included_in_bundle"]]
    paid = [a for a in assets if a["paid"]]
    counts = {
        "total": len(assets),
        "included": len(included),
        "paid_excluded": len(paid),
        "free": sum(1 for a in assets if not a["paid"]),
        "packs": len({a["pack"] for a in assets}),
        "uses": len({a["use"] for a in assets}),
        "bytes_included": sum(int(a["bytes"]) for a in included),
    }
    manifest: Dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "bundle_id": _text(bundle_id) or "office-assets",
        "assets": assets,
        "counts": counts,
        "policy": {
            "provenance_required": True,
            "paid_art_local_only": True,
            "paid_art_redistributed": False,
            "credit_required": True,
            "credit": credit,
        },
    }
    manifest["digest"] = compute_digest(manifest)
    return manifest


def validate_bundle(manifest: Mapping[str, Any]) -> List[str]:
    """Return every defect in a built manifest, or ``[]``."""
    problems: List[str] = []
    if not isinstance(manifest, Mapping):
        return ["manifest is not a mapping"]
    if manifest.get("schema") != SCHEMA:
        problems.append(f"schema: {manifest.get('schema')!r} != {SCHEMA!r}")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        return problems + ["assets: manifest has no assets"]
    policy = manifest.get("policy") or {}
    if policy.get("paid_art_redistributed") is not False:
        problems.append("policy: paid_art_redistributed must be false")
    if policy.get("provenance_required") is not True:
        problems.append("policy: provenance_required must be true")
    if manifest.get("digest") and manifest["digest"] != compute_digest(
            manifest):
        problems.append("digest: does not match the manifest")
    seen: set[str] = set()
    for index, asset in enumerate(assets):
        where = f"asset {asset.get('asset_id') or index!r}"
        asset_id = _text(asset.get("asset_id"))
        if not asset_id:
            problems.append(f"{where}: no asset_id")
        elif asset_id in seen:
            problems.append(f"{where}: duplicate asset id")
        seen.add(asset_id)
        if not _text(asset.get("pack")):
            problems.append(f"{where}: no pack")
        if not _SHA_RE.match(_text(asset.get("sha256"))):
            problems.append(f"{where}: sha256 is not 64 lowercase hex")
        if not _text(asset.get("license")):
            problems.append(f"{where}: no licence")
        if asset.get("use") not in USES:
            problems.append(f"{where}: invalid use {asset.get('use')!r}")
        if asset.get("paid"):
            if asset.get("included_in_bundle") is not False:
                problems.append(f"{where}: paid art must not be bundled")
            if asset.get("redistribution_allowed") is not False:
                problems.append(
                    f"{where}: paid art must not be redistributable")
            if not asset.get("local_only"):
                problems.append(f"{where}: paid art must be local-only")
    return problems


# ---------------------------------------------------------------- leak scan
@dataclass(frozen=True)
class LeakFinding:
    path: str
    kind: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class LeakReport:
    roots: Tuple[str, ...]
    checked_files: int
    findings: Tuple[LeakFinding, ...]
    truncated: bool = False
    schema: str = LEAK_SCHEMA

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "roots": list(self.roots),
            "checked_files": int(self.checked_files),
            "truncated": bool(self.truncated),
            "findings": [f.to_dict() for f in self.findings],
        }


def _iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in {".git", "node_modules", "__pycache__"})
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _leak_name_match(relative: str) -> Optional[str]:
    normalised = _normalise_pack(relative)
    for alias in PAID_PACK_ALIASES:
        alias_norm = _normalise_pack(alias)
        if alias_norm and alias_norm in normalised:
            return alias
    return None


def scan_for_leaks(
    root: Path | str,
    *,
    paid_hashes: Sequence[str] = (),
    forbidden_markers: Sequence[str] = FORBIDDEN_TEXT_MARKERS,
    max_files: int = _MAX_SCAN_FILES,
) -> LeakReport:
    """Scan one local target tree for paid-art leakage.

    Deterministic: findings are sorted by path then kind. Reads only file names,
    a bounded sha256, and small text bodies for path references.
    """
    base = Path(root)
    findings: List[LeakFinding] = []
    checked = 0
    truncated = False
    hashes = {h.lower() for h in paid_hashes if h}
    markers = [m.lower() for m in forbidden_markers if m]
    if base.is_file():
        candidates: Iterable[Path] = [base]
    elif base.exists():
        candidates = _iter_files(base)
    else:
        return LeakReport(roots=(str(base),), checked_files=0,
                          findings=())
    for path in candidates:
        if checked >= max_files:
            truncated = True
            break
        checked += 1
        try:
            relative = path.relative_to(base).as_posix()
        except ValueError:
            relative = path.as_posix()
        suffix = path.suffix.lower()
        alias = _leak_name_match(relative)
        if alias:
            findings.append(LeakFinding(
                relative, "paid_pack_name",
                f"path names paid pack {alias!r}"))
        if suffix in LEAK_EXTENSIONS:
            findings.append(LeakFinding(
                relative, "archive_or_binary",
                f"{suffix} is never redistributed"))
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if hashes and size and size <= _MAX_HASH_BYTES:
            try:
                digest = _sha256_file(path)
            except OSError:
                digest = ""
            if digest and digest in hashes:
                findings.append(LeakFinding(
                    relative, "paid_asset_hash",
                    f"sha256 {digest[:12]}… matches a paid asset"))
        if markers and suffix in TEXT_EXTENSIONS and size <= _MAX_TEXT_BYTES:
            try:
                body = path.read_text("utf-8", errors="ignore").lower()
            except OSError:
                body = ""
            for marker in markers:
                if marker and marker in body:
                    findings.append(LeakFinding(
                        relative, "paid_path_reference",
                        "text references the operator paid-art root"))
                    break
    findings.sort(key=lambda f: (f.path, f.kind))
    return LeakReport(
        roots=(str(base),), checked_files=checked,
        findings=tuple(findings), truncated=truncated)


def scan_targets(
    targets: Mapping[str, Path | str],
    *,
    paid_hashes: Sequence[str] = (),
    forbidden_markers: Sequence[str] = FORBIDDEN_TEXT_MARKERS,
) -> Dict[str, Any]:
    """Scan named targets (bundle/cache/screenshots/backups/exports).

    A missing target is skipped, not fabricated: the report names only targets
    that exist. ``ok`` is true only when every scanned target is clean.
    """
    reports: Dict[str, Any] = {}
    findings: List[Dict[str, Any]] = []
    checked = 0
    truncated = False
    for label, target in targets.items():
        if target is None:
            continue
        path = Path(target)
        if not path.exists():
            continue
        report = scan_for_leaks(
            path, paid_hashes=paid_hashes,
            forbidden_markers=forbidden_markers)
        reports[label] = report.to_dict()
        checked += report.checked_files
        truncated = truncated or report.truncated
        for finding in report.findings:
            row = finding.to_dict()
            row["target"] = label
            findings.append(row)
    findings.sort(key=lambda row: (row.get("target", ""), row["path"],
                                   row["kind"]))
    return {
        "schema": LEAK_SCHEMA,
        "ok": not findings,
        "checked_files": checked,
        "truncated": truncated,
        "targets": reports,
        "findings": findings,
    }
