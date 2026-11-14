"""The one canonical CharacterProfile and its single save authority (P3-07).

There is exactly **one** editor record for a Qubit Office character. Habblaud's
character state and the LimeZu character/portrait generators are projections
over this profile; neither owns a save path. This module is deliberately small
and local:

* :class:`CharacterProfile` — the canonical record (parts, palette, layers,
  shadow policy, portrait cell/size, animation set, style version, seed,
  revision, and local asset references only).
* :func:`build_part_catalog` — the measured LimeZu part catalog
  (body 9 / eyes 7 / hair 29 / outfit 33 / skin 9). Metadata only: **no art is
  opened, copied or committed.**
* :class:`CharacterProfileStore` — filesystem JSON under a caller-supplied
  state directory, atomic and ``0600``, with append-only revisions, rollback,
  import/export, fallback resolution and a paid-art guard.
* :func:`migrate_profile_dict` — versioned revision migration with an explicit
  step list.

Honesty notes
-------------
* ``sha256`` on a part/source reference is a **manifest digest** over
  ``pack/category/part``. It is a stable identity for drift detection, not a
  claim about decoded pixels; reading paid art is out of scope here.
* The paid-art guard checks path containment, not a licence grant. Art still
  needs the owner's recorded licence (see ``THIRD_PARTY_NOTICES.md`` and
  ``LICENSE-ASSETS``), and paid packs are never shipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

__all__ = [
    "SCHEMA",
    "STORE_SCHEMA",
    "EXPORT_SCHEMA",
    "STYLE_VERSION",
    "PART_CATEGORIES",
    "PART_COUNTS",
    "ANIMATIONS",
    "SHADOW_POLICIES",
    "LAYER_ORDER",
    "CharacterProfileError",
    "PaidArtRefused",
    "StaleProfileError",
    "UnknownProfileError",
    "RevisionNotFound",
    "UnsafeDestination",
    "StoreCorruptError",
    "CharacterPart",
    "SourceAssetRef",
    "CharacterProfile",
    "build_part_catalog",
    "part_counts",
    "default_profile",
    "validate_profile",
    "apply_fallbacks",
    "migrate_profile_dict",
    "CharacterProfileStore",
]

SCHEMA = "qubit-office-character-profile/1"
STORE_SCHEMA = "qubit-office-character-profile-store/1"
EXPORT_SCHEMA = "qubit-office-character-profile-export/1"
STYLE_VERSION = 1

PART_CATEGORIES: Tuple[str, ...] = ("body", "skin", "eyes", "hair", "outfit")
PART_COUNTS: Mapping[str, int] = {
    "body": 9,
    "skin": 9,
    "eyes": 7,
    "hair": 29,
    "outfit": 33,
}
ACCESSORY_COUNT = 6

ANIMATIONS: Tuple[str, ...] = (
    "idle", "walk", "type", "talk", "think", "break", "celebrate",
)
SHADOW_POLICIES: Tuple[str, ...] = ("none", "sprite", "room")
LAYER_ORDER: Tuple[str, ...] = (
    "body", "outfit", "skin", "eyes", "hair", "accessory",
)
DEFAULT_PALETTE: Tuple[str, ...] = (
    "#1f2933", "#3e4c59", "#f0b429", "#7fb3d5",
)

_PACK_CHARACTER = "Character Generator 2.0 Linux Build"
_PACK_PORTRAIT = "Portrait_Generator_1.5.0_Linux_Build"
_LOCAL_ROOT = "/home/trick/Desktop/UI Office"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# --------------------------------------------------------------------- errors
class CharacterProfileError(ValueError):
    """Invalid profile data. ``problems`` names every reason."""

    code = "CHARACTER_PROFILE_INVALID"

    def __init__(self, problems: Iterable[str] | str, *, code: str = ""):
        if isinstance(problems, str):
            problems = [problems]
        self.problems: List[str] = list(problems)
        if code:
            self.code = code
        super().__init__("; ".join(self.problems) or self.code)


class PaidArtRefused(CharacterProfileError):
    """A paid asset would enter the repository/public tree."""

    code = "PAID_ART_REFUSED"


class StaleProfileError(CharacterProfileError):
    """The caller edited an older revision than the store currently holds."""

    code = "CHARACTER_PROFILE_STALE"


class UnknownProfileError(CharacterProfileError):
    code = "CHARACTER_PROFILE_NOT_FOUND"


class RevisionNotFound(CharacterProfileError):
    code = "CHARACTER_REVISION_NOT_FOUND"


class UnsafeDestination(CharacterProfileError):
    code = "CHARACTER_EXPORT_UNSAFE"


class StoreCorruptError(CharacterProfileError):
    code = "CHARACTER_STORE_CORRUPT"


# ------------------------------------------------------------------ catalog
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _manifest_digest(pack: str, category: str, part_id: str) -> str:
    return hashlib.sha256(
        f"{pack}/{category}/{part_id}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CharacterPart:
    part_id: str
    category: str
    index: int
    label: str
    local_path: str
    sha256: str
    paid: bool = True
    local_only: bool = True
    pack: str = _PACK_CHARACTER

    def to_dict(self) -> Dict[str, Any]:
        return {
            "part_id": self.part_id,
            "category": self.category,
            "index": int(self.index),
            "label": self.label,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "paid": bool(self.paid),
            "local_only": bool(self.local_only),
            "pack": self.pack,
        }


def build_part_catalog() -> Tuple[CharacterPart, ...]:
    """Build the measured LimeZu part catalog (metadata only)."""
    rows: List[CharacterPart] = []
    for category in PART_CATEGORIES:
        for index in range(PART_COUNTS[category]):
            part_id = f"{category}-{index:02d}"
            rows.append(CharacterPart(
                part_id=part_id,
                category=category,
                index=index,
                label=f"{category.title()} {index + 1:02d}",
                local_path=(
                    f"{_LOCAL_ROOT}/{_PACK_CHARACTER}/{category}/"
                    f"{part_id}.png"),
                sha256=_manifest_digest(_PACK_CHARACTER, category, part_id),
            ))
    for index in range(ACCESSORY_COUNT):
        part_id = f"accessory-{index:02d}"
        rows.append(CharacterPart(
            part_id=part_id,
            category="accessory",
            index=index,
            label=f"Accessory {index + 1:02d}",
            local_path=(
                f"{_LOCAL_ROOT}/{_PACK_CHARACTER}/accessory/{part_id}.png"),
            sha256=_manifest_digest(_PACK_CHARACTER, "accessory", part_id),
        ))
    return tuple(rows)


CATALOG: Tuple[CharacterPart, ...] = build_part_catalog()
CATALOG_INDEX: Mapping[str, CharacterPart] = {
    part.part_id: part for part in CATALOG}


def part_counts() -> Dict[str, int]:
    counts = {category: 0 for category in (*PART_CATEGORIES, "accessory")}
    for part in CATALOG:
        counts[part.category] = counts.get(part.category, 0) + 1
    return counts


def _first_part(category: str) -> str:
    for part in CATALOG:
        if part.category == category:
            return part.part_id
    raise CharacterProfileError(f"catalog has no {category} parts")


# -------------------------------------------------------------- value types
@dataclass(frozen=True)
class SourceAssetRef:
    path: str
    sha256: str
    paid: bool
    local_only: bool
    pack: str = ""
    part_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "paid": bool(self.paid),
            "local_only": bool(self.local_only),
            "pack": self.pack,
            "part_id": self.part_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceAssetRef":
        return cls(
            path=str(data.get("path", "")),
            sha256=str(data.get("sha256", "")),
            paid=bool(data.get("paid", False)),
            local_only=bool(data.get("local_only", False)),
            pack=str(data.get("pack", "")),
            part_id=str(data.get("part_id", "")),
        )


def _refs_for_parts(part_ids: Sequence[str]) -> Tuple[SourceAssetRef, ...]:
    refs: List[SourceAssetRef] = []
    for part_id in part_ids:
        part = CATALOG_INDEX.get(part_id)
        if part is None:
            continue
        refs.append(SourceAssetRef(
            path=part.local_path,
            sha256=part.sha256,
            paid=part.paid,
            local_only=part.local_only,
            pack=part.pack,
            part_id=part.part_id,
        ))
    return tuple(refs)


@dataclass(frozen=True)
class CharacterProfile:
    profile_id: str
    agent_id: str
    body_id: str = "body-00"
    skin_id: str = "skin-00"
    eyes_id: str = "eyes-00"
    hair_id: str = "hair-00"
    outfit_id: str = "outfit-00"
    accessory_ids: Tuple[str, ...] = ()
    palette: Tuple[str, ...] = DEFAULT_PALETTE
    layer_order: Tuple[str, ...] = LAYER_ORDER
    shadow_policy: str = "sprite"
    portrait_cell: str = "cell-0"
    portrait_size: int = 32
    animation_set: Tuple[str, ...] = ANIMATIONS
    style_version: int = STYLE_VERSION
    seed: int = 0
    revision: int = 0
    source_asset_refs: Tuple[SourceAssetRef, ...] = ()
    schema: str = SCHEMA

    def part_ids(self) -> Tuple[str, ...]:
        return (self.body_id, self.skin_id, self.eyes_id, self.hair_id,
                self.outfit_id, *self.accessory_ids)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "profile_id": self.profile_id,
            "agent_id": self.agent_id,
            "body_id": self.body_id,
            "skin_id": self.skin_id,
            "eyes_id": self.eyes_id,
            "hair_id": self.hair_id,
            "outfit_id": self.outfit_id,
            "accessory_ids": list(self.accessory_ids),
            "palette": list(self.palette),
            "layer_order": list(self.layer_order),
            "shadow_policy": self.shadow_policy,
            "portrait_cell": self.portrait_cell,
            "portrait_size": int(self.portrait_size),
            "animation_set": list(self.animation_set),
            "style_version": int(self.style_version),
            "seed": int(self.seed),
            "revision": int(self.revision),
            "source_asset_refs": [ref.to_dict()
                                  for ref in self.source_asset_refs],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CharacterProfile":
        migrated, _ = migrate_profile_dict(data)
        return cls(
            profile_id=str(migrated.get("profile_id", "")),
            agent_id=str(migrated.get("agent_id", "")),
            body_id=str(migrated.get("body_id", "body-00")),
            skin_id=str(migrated.get("skin_id", "skin-00")),
            eyes_id=str(migrated.get("eyes_id", "eyes-00")),
            hair_id=str(migrated.get("hair_id", "hair-00")),
            outfit_id=str(migrated.get("outfit_id", "outfit-00")),
            accessory_ids=tuple(str(a) for a in
                                migrated.get("accessory_ids", ())),
            palette=tuple(str(c) for c in
                          migrated.get("palette", DEFAULT_PALETTE)),
            layer_order=tuple(str(x) for x in
                              migrated.get("layer_order", LAYER_ORDER)),
            shadow_policy=str(migrated.get("shadow_policy", "sprite")),
            portrait_cell=str(migrated.get("portrait_cell", "cell-0")),
            portrait_size=int(migrated.get("portrait_size", 32)),
            animation_set=tuple(str(a) for a in
                                migrated.get("animation_set", ANIMATIONS)),
            style_version=int(migrated.get("style_version",
                                           STYLE_VERSION)),
            seed=int(migrated.get("seed", 0)),
            revision=int(migrated.get("revision", 0)),
            source_asset_refs=tuple(
                SourceAssetRef.from_dict(ref)
                for ref in migrated.get("source_asset_refs", ())),
            schema=str(migrated.get("schema", SCHEMA)),
        )


def default_profile(profile_id: str = "profile-default",
                    agent_id: str = "agent-default") -> CharacterProfile:
    picks = ("body-00", "skin-00", "eyes-00", "hair-00", "outfit-00")
    return CharacterProfile(
        profile_id=profile_id,
        agent_id=agent_id,
        source_asset_refs=_refs_for_parts(picks),
    )


# ----------------------------------------------------------------- migration
_LEGACY_KEYS = {
    "body": "body_id",
    "skin": "skin_id",
    "eyes": "eyes_id",
    "hair": "hair_id",
    "outfit": "outfit_id",
    "colors": "palette",
    "layers": "layer_order",
}


def migrate_profile_dict(
        data: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Migrate a raw profile dict to :data:`STYLE_VERSION`.

    Returns ``(migrated, steps)``. Legacy aliases are renamed and a pre-v1
    profile gets explicit defaults; current data is only normalised. The
    migration never invents an agent or profile identity.
    """
    src: Dict[str, Any] = dict(data or {})
    steps: List[str] = []

    for legacy, modern in _LEGACY_KEYS.items():
        if legacy in src and modern not in src:
            src[modern] = src.pop(legacy)
            steps.append(f"renamed {legacy!r} -> {modern!r}")

    try:
        version = int(src.get("style_version", 0))
    except (TypeError, ValueError):
        version = 0
    if version < STYLE_VERSION:
        defaults = {
            "accessory_ids": [],
            "palette": list(DEFAULT_PALETTE),
            "layer_order": list(LAYER_ORDER),
            "shadow_policy": "sprite",
            "portrait_cell": "cell-0",
            "portrait_size": 32,
            "animation_set": list(ANIMATIONS),
            "seed": 0,
            "revision": 0,
        }
        filled = [key for key, value in defaults.items() if key not in src]
        for key in filled:
            src[key] = defaults[key]
        if "source_asset_refs" not in src:
            picks = [str(src.get(f"{c}_id", _first_part(c)))
                     for c in PART_CATEGORIES]
            src["source_asset_refs"] = [
                ref.to_dict() for ref in _refs_for_parts(picks)]
            filled.append("source_asset_refs")
        steps.append(
            f"migrated style_version {version} -> {STYLE_VERSION}"
            + (f"; filled {', '.join(sorted(filled))}" if filled else ""))

    src["style_version"] = STYLE_VERSION
    src.setdefault("schema", SCHEMA)
    try:
        src["portrait_size"] = int(src.get("portrait_size", 32))
    except (TypeError, ValueError):
        src["portrait_size"] = 32
    return src, steps


# ---------------------------------------------------------------- validation
def _guard_refs(profile: CharacterProfile, root: Path) -> List[str]:
    problems: List[str] = []
    for ref in profile.source_asset_refs:
        where = f"source ref {ref.path or '<empty>'!r}"
        if not ref.path:
            problems.append(f"{where}: empty path")
        if not _SHA_RE.match(ref.sha256 or ""):
            problems.append(f"{where}: sha256 must be 64 lowercase hex")
        if ref.paid:
            if not ref.local_only:
                problems.append(f"{where}: paid art must be local_only")
            path = Path(ref.path)
            if not path.is_absolute():
                problems.append(
                    f"{where}: paid art path must be absolute and local")
            elif _inside(path, root):
                problems.append(
                    f"{where}: paid art path is inside the repository"
                    f" (public tree)")
        elif ref.local_only and ref.path:
            path = Path(ref.path)
            if path.is_absolute() and _inside(path, root):
                problems.append(
                    f"{where}: local-only path is inside the repository")
    return problems


def validate_profile(profile: CharacterProfile,
                     *, repo_root: Path | str | None = None) -> List[str]:
    """Every reason a profile is invalid, or ``[]``."""
    root = Path(repo_root) if repo_root is not None else _repo_root()
    problems: List[str] = []

    if profile.schema != SCHEMA:
        problems.append(f"schema: {profile.schema!r} != {SCHEMA!r}")
    if not _ID_RE.match(profile.profile_id or ""):
        problems.append(f"profile_id {profile.profile_id!r}: invalid id")
    if not _ID_RE.match(profile.agent_id or ""):
        problems.append(f"agent_id {profile.agent_id!r}: invalid id")

    for category in PART_CATEGORIES:
        part_id = getattr(profile, f"{category}_id")
        part = CATALOG_INDEX.get(part_id)
        if part is None:
            problems.append(f"{category}_id {part_id!r}: unknown part")
        elif part.category != category:
            problems.append(
                f"{category}_id {part_id!r}: part belongs to"
                f" {part.category!r}")

    for accessory in profile.accessory_ids:
        part = CATALOG_INDEX.get(accessory)
        if part is None or part.category != "accessory":
            problems.append(f"accessory {accessory!r}: unknown part")

    if not (1 <= len(profile.palette) <= 8):
        problems.append("palette: expected 1..8 colours")
    for colour in profile.palette:
        if not _HEX_RE.match(colour or ""):
            problems.append(f"palette: {colour!r} is not #rrggbb")

    if len(set(profile.layer_order)) != len(profile.layer_order):
        problems.append("layer_order: duplicate layer")
    if set(profile.layer_order) != set(LAYER_ORDER):
        problems.append(
            f"layer_order: expected a permutation of {LAYER_ORDER}, got"
            f" {profile.layer_order}")

    if profile.shadow_policy not in SHADOW_POLICIES:
        problems.append(
            f"shadow_policy {profile.shadow_policy!r} not in"
            f" {SHADOW_POLICIES}")
    if profile.portrait_size not in (32, 48):
        problems.append(
            f"portrait_size {profile.portrait_size} must be 32 or 48")
    if not profile.portrait_cell:
        problems.append("portrait_cell: empty")

    if not profile.animation_set:
        problems.append("animation_set: empty")
    for animation in profile.animation_set:
        if animation not in ANIMATIONS:
            problems.append(f"animation_set: unknown {animation!r}")
    if "idle" not in profile.animation_set:
        problems.append("animation_set: idle is required")

    if profile.style_version != STYLE_VERSION:
        problems.append(
            f"style_version {profile.style_version} != {STYLE_VERSION}"
            f" (migrate before saving)")
    if profile.seed < 0:
        problems.append("seed: must be >= 0")
    if profile.revision < 0:
        problems.append("revision: must be >= 0")

    problems.extend(_guard_refs(profile, root))
    return problems


# ------------------------------------------------------------------ fallback
def apply_fallbacks(
        profile: CharacterProfile, *,
        repo_root: Path | str | None = None,
) -> Tuple[CharacterProfile, List[Dict[str, str]]]:
    """Resolve missing/renamed parts deterministically.

    A missing part falls back to the first part of its category; a missing
    accessory is dropped; an invalid layer order, portrait size or animation
    set falls back to the documented default. Every substitution is returned
    so the studio can show it instead of silently changing the character.
    """
    fallbacks: List[Dict[str, str]] = []
    updates: Dict[str, Any] = {}

    for category in PART_CATEGORIES:
        attr = f"{category}_id"
        current = getattr(profile, attr)
        part = CATALOG_INDEX.get(current)
        if part is None or part.category != category:
            target = _first_part(category)
            updates[attr] = target
            fallbacks.append({
                "category": category,
                "from": current,
                "to": target,
                "reason": "missing_part",
            })

    kept_accessories: List[str] = []
    for accessory in profile.accessory_ids:
        part = CATALOG_INDEX.get(accessory)
        if part is None or part.category != "accessory":
            fallbacks.append({
                "category": "accessory",
                "from": accessory,
                "to": "",
                "reason": "missing_part",
            })
        else:
            kept_accessories.append(accessory)

    if (len(set(profile.layer_order)) != len(profile.layer_order)
            or set(profile.layer_order) != set(LAYER_ORDER)):
        updates["layer_order"] = LAYER_ORDER
        fallbacks.append({
            "category": "layer_order",
            "from": ",".join(profile.layer_order),
            "to": ",".join(LAYER_ORDER),
            "reason": "invalid_layer_order",
        })

    if profile.portrait_size not in (32, 48):
        updates["portrait_size"] = 32
        fallbacks.append({
            "category": "portrait_size",
            "from": str(profile.portrait_size),
            "to": "32",
            "reason": "invalid_portrait_size",
        })

    animations = tuple(a for a in profile.animation_set
                       if a in ANIMATIONS)
    if not animations or "idle" not in animations:
        animations = ANIMATIONS
        fallbacks.append({
            "category": "animation_set",
            "from": ",".join(profile.animation_set),
            "to": ",".join(ANIMATIONS),
            "reason": "invalid_animation_set",
        })

    resolved = replace(
        profile,
        accessory_ids=tuple(kept_accessories),
        animation_set=animations,
        **updates,
    )
    return resolved, fallbacks


# -------------------------------------------------------------------- store
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise UnknownProfileError(
            f"no character profile at {path.name}", code=(
                "CHARACTER_PROFILE_NOT_FOUND")) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreCorruptError(
            f"{path.name}: {exc}", code="CHARACTER_STORE_CORRUPT") from exc


class CharacterProfileStore:
    """The one save authority for character profiles.

    Files live under ``<state_dir>/character_profiles/<profile_id>.json`` and
    are written atomically with mode ``0600`` (the directory is ``0700``).
    Revisions are append-only; rollback appends a new revision rather than
    rewriting history.
    """

    def __init__(self, state_dir: Path | str, *,
                 repo_root: Path | str | None = None,
                 max_revisions: int = 64,
                 clock=None):
        if max_revisions < 1:
            raise CharacterProfileError(
                "max_revisions must be >= 1", code="CHARACTER_STORE_CONFIG")
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "character_profiles"
        self.repo_root = Path(repo_root) if repo_root is not None else (
            _repo_root())
        self.max_revisions = int(max_revisions)
        self._clock = clock or _now_iso

    # -- paths ------------------------------------------------------------
    def path_for(self, profile_id: str) -> Path:
        if not _ID_RE.match(profile_id or ""):
            raise CharacterProfileError(
                f"profile_id {profile_id!r}: invalid id",
                code="CHARACTER_PROFILE_INVALID")
        return self.root / f"{profile_id}.json"

    def _document(self, profile_id: str) -> Dict[str, Any]:
        doc = _read(self.path_for(profile_id))
        if doc.get("schema") != STORE_SCHEMA:
            raise StoreCorruptError(
                f"{profile_id}: unknown store schema"
                f" {doc.get('schema')!r}")
        if not doc.get("revisions"):
            raise StoreCorruptError(f"{profile_id}: no revisions")
        return doc

    def _current_entry(self, doc: Mapping[str, Any]) -> Mapping[str, Any]:
        current = int(doc.get("current_revision", 0))
        for entry in doc.get("revisions", ()):
            if int(entry.get("revision", -1)) == current:
                return entry
        raise StoreCorruptError(
            f"current_revision {current} is missing from revisions")

    # -- reads ------------------------------------------------------------
    def exists(self, profile_id: str) -> bool:
        return self.path_for(profile_id).exists()

    def load(self, profile_id: str) -> CharacterProfile:
        doc = self._document(profile_id)
        entry = self._current_entry(doc)
        return CharacterProfile.from_dict(entry["profile"])

    def load_resolved(
            self, profile_id: str,
    ) -> Tuple[CharacterProfile, List[Dict[str, str]]]:
        return apply_fallbacks(self.load(profile_id),
                               repo_root=self.repo_root)

    def list_revisions(self, profile_id: str) -> List[Dict[str, Any]]:
        doc = self._document(profile_id)
        rows: List[Dict[str, Any]] = []
        for entry in doc.get("revisions", ()):
            rows.append({
                "revision": int(entry.get("revision", 0)),
                "kind": str(entry.get("kind", "save")),
                "saved_at": str(entry.get("saved_at", "")),
                "rollback_of": entry.get("rollback_of"),
                "current": int(entry.get("revision", 0))
                == int(doc.get("current_revision", 0)),
            })
        return rows

    def get_revision(self, profile_id: str,
                     revision: int) -> CharacterProfile:
        doc = self._document(profile_id)
        for entry in doc.get("revisions", ()):
            if int(entry.get("revision", -1)) == int(revision):
                return CharacterProfile.from_dict(entry["profile"])
        raise RevisionNotFound(
            f"{profile_id}: no revision {revision}",
            code="CHARACTER_REVISION_NOT_FOUND")

    # -- writes -----------------------------------------------------------
    def _guard_repo(self, profile: CharacterProfile) -> None:
        problems = _guard_refs(profile, self.repo_root)
        if problems:
            raise PaidArtRefused(
                problems, code="PAID_ART_REFUSED")
        if any(ref.paid for ref in profile.source_asset_refs) and _inside(
                self.root, self.repo_root):
            raise PaidArtRefused(
                f"the profile state directory {self.root} is inside the"
                f" repository; paid-art profiles may not be committed",
                code="PAID_ART_REFUSED")

    def save(self, profile: CharacterProfile) -> CharacterProfile:
        # Paid-art containment is checked first so the refusal is the typed
        # ``PAID_ART_REFUSED`` (not a generic validation list).
        self._guard_repo(profile)
        problems = validate_profile(profile, repo_root=self.repo_root)
        if problems:
            raise CharacterProfileError(problems)

        path = self.path_for(profile.profile_id)
        if path.exists():
            doc = self._document(profile.profile_id)
            current = int(doc.get("current_revision", 0))
            if profile.revision != current:
                raise StaleProfileError(
                    f"{profile.profile_id}: revision {profile.revision}"
                    f" is stale; current is {current}",
                    code="CHARACTER_PROFILE_STALE")
            next_revision = int(doc.get("next_revision", current + 1))
            revisions = list(doc.get("revisions", ()))
        else:
            current = 0
            next_revision = 1
            revisions = []

        stored = replace(profile, revision=next_revision,
                         style_version=STYLE_VERSION)
        revisions.append({
            "revision": next_revision,
            "kind": "save",
            "saved_at": self._clock(),
            "profile": stored.to_dict(),
        })
        if len(revisions) > self.max_revisions:
            revisions = revisions[-self.max_revisions:]

        doc = {
            "schema": STORE_SCHEMA,
            "profile_id": profile.profile_id,
            "agent_id": profile.agent_id,
            "current_revision": next_revision,
            "next_revision": next_revision + 1,
            "revisions": revisions,
        }
        _atomic_write(path, doc)
        return stored

    def rollback(self, profile_id: str, revision: int) -> CharacterProfile:
        doc = self._document(profile_id)
        target = None
        for entry in doc.get("revisions", ()):
            if int(entry.get("revision", -1)) == int(revision):
                target = entry
                break
        if target is None:
            raise RevisionNotFound(
                f"{profile_id}: no revision {revision}",
                code="CHARACTER_REVISION_NOT_FOUND")

        source = CharacterProfile.from_dict(target["profile"])
        next_revision = int(doc.get("next_revision", 0)) or (
            int(doc.get("current_revision", 0)) + 1)
        stored = replace(source, revision=next_revision,
                         style_version=STYLE_VERSION)
        self._guard_repo(stored)
        revisions = list(doc.get("revisions", ()))
        revisions.append({
            "revision": next_revision,
            "kind": "rollback",
            "rollback_of": int(revision),
            "saved_at": self._clock(),
            "profile": stored.to_dict(),
        })
        if len(revisions) > self.max_revisions:
            revisions = revisions[-self.max_revisions:]
        doc.update({
            "current_revision": next_revision,
            "next_revision": next_revision + 1,
            "revisions": revisions,
        })
        _atomic_write(self.path_for(profile_id), doc)
        return stored

    # -- import / export --------------------------------------------------
    def export_document(self, profile_id: str) -> Dict[str, Any]:
        doc = self._document(profile_id)
        return {
            "schema": EXPORT_SCHEMA,
            "exported_at": self._clock(),
            "profile": dict(self._current_entry(doc)["profile"]),
            "revisions": [
                {
                    "revision": int(entry.get("revision", 0)),
                    "kind": str(entry.get("kind", "save")),
                    "saved_at": str(entry.get("saved_at", "")),
                    "rollback_of": entry.get("rollback_of"),
                    "profile": dict(entry.get("profile", {})),
                }
                for entry in doc.get("revisions", ())
            ],
        }

    def _assert_destination(self, destination: Path,
                            document: Mapping[str, Any]) -> None:
        if _inside(destination, self.repo_root):
            refs = [
                SourceAssetRef.from_dict(ref)
                for ref in document.get("profile", {}).get(
                    "source_asset_refs", ())
            ]
            if any(ref.paid for ref in refs):
                raise PaidArtRefused(
                    "refusing to export a paid-art profile inside the"
                    " repository/public tree",
                    code="PAID_ART_REFUSED")
        if destination.exists() and destination.is_dir():
            raise UnsafeDestination(
                f"export destination {destination} is a directory",
                code="CHARACTER_EXPORT_UNSAFE")

    def export(self, profile_id: str,
               destination: Path | str | None = None) -> Dict[str, Any]:
        document = self.export_document(profile_id)
        if destination is None:
            return document
        dest = Path(destination)
        self._assert_destination(dest, document)
        _atomic_write(dest, document)
        return document

    def import_document(
            self, document: Mapping[str, Any]) -> CharacterProfile:
        if document.get("schema") == EXPORT_SCHEMA:
            source = document.get("profile")
        else:
            source = document
        if not isinstance(source, Mapping):
            raise CharacterProfileError(
                "import: no profile object", code="CHARACTER_PROFILE_INVALID")
        migrated, _ = migrate_profile_dict(source)
        profile = CharacterProfile.from_dict(migrated)
        # An import is a new local history entry; the incoming revision number
        # is not trusted as a local pointer.
        profile = replace(profile, revision=0)
        return self.save(profile)

    def import_file(self, path: Path | str) -> CharacterProfile:
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise CharacterProfileError(
                f"import {path}: {exc}",
                code="CHARACTER_PROFILE_INVALID") from exc
        return self.import_document(document)
