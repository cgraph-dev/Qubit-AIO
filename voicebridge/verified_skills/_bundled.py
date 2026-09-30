"""Bundled Qubit skill manifests (Qubit delta closure A46).

Qubit ships a small set of human-reviewed, product-bundled skills under the
repository ``skills/`` directory. This module is the verified-skills package's
read-only *bundled skill registry*: it really loads and validates each skill's
``SKILL.md`` YAML front matter (no network, no execution), and exposes the
validated manifests to the CLI/dashboard/voice listing surfaces.

Design boundaries:

* A bundled skill is Qubit-authored/adapted instructional material, not an
  agent-authored memory record. It is therefore **never** inserted into the
  verified-skill SQLite memory (that would pollute recall and create a
  self-verification path around the owner-only verdict gate). ``list()`` and
  ``recall()`` on the advisory store are unchanged; callers use
  :func:`bundled_skill_manifests` / :meth:`VerifiedSkillStore.bundled`.
* Loading is bounded and fail-closed: a missing/oversized/malformed manifest is
  a typed :class:`BundledSkillError`, never a traceback.
* Display fields must be Qubit-branded. Upstream project names are rejected in
  the user-facing ``name``/``description``/``label`` fields; provenance records
  (this package's docs, ``skills/*/NOTICE``, ``skills/SOURCES.md``) may retain
  the required legal attribution.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._models import VerifiedSkillError

BUNDLED_SKILL_SCHEMA = "qubit-bundled-skill/1"
MAX_BUNDLED_MANIFEST_BYTES = 262144

# The bundled skills Qubit ships. Order is stable for deterministic output.
BUNDLED_SKILLS = ("visualize",)

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
# Upstream product names that must not appear on the Qubit product surface.
# Assembled from fragments so the de-brand engine's prose pass cannot rewrite
# the guard into matching "Qubit" itself.
_UPSTREAM_BRAND_RE = re.compile(
    r"\b(?:" + "Her" + "mes" + "|" + "No" + "us" + "|" + "Open" + "Claw"
    + r")\b", re.IGNORECASE)
_REQUIRED_FIELDS = ("name", "description")
_MAX_DESCRIPTION = 600
_MAX_CAPABILITIES = 32


class BundledSkillError(VerifiedSkillError):
    """Typed bundled-skill manifest failure."""


def bundled_skills_root() -> Path:
    """Repository ``skills/`` directory that holds the bundled skills."""
    return Path(__file__).resolve().parents[2] / "skills"


def bundled_skill_names() -> List[str]:
    return list(BUNDLED_SKILLS)


def _check_name(name: Any) -> str:
    clean = str(name or "").strip().lower()
    if clean not in BUNDLED_SKILLS:
        raise BundledSkillError(f"unknown bundled skill {name!r}")
    return clean


def _check_branded(value: str, field: str) -> str:
    if _UPSTREAM_BRAND_RE.search(value or ""):
        raise BundledSkillError(
            f"bundled skill {field} exposes an upstream product name on the "
            "Qubit surface; keep provenance in skills/*/NOTICE and SOURCES.md")
    return value


def parse_skill_front_matter(text: str) -> Dict[str, Any]:
    """Parse a ``SKILL.md`` YAML front matter block (bounded, no execution)."""
    if not isinstance(text, str) or not text.startswith("---"):
        raise BundledSkillError("SKILL.md is missing a YAML front matter block")
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise BundledSkillError("SKILL.md front matter is not terminated")
    block = "\n".join(lines[1:end])
    try:
        import yaml as _yaml

        data = _yaml.safe_load(block) or {}
    except Exception as exc:  # noqa: BLE001 - typed at the boundary
        raise BundledSkillError(f"SKILL.md front matter invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise BundledSkillError("SKILL.md front matter must be a mapping")
    return data


def validate_bundled_skill_manifest(manifest: Any) -> Dict[str, Any]:
    """Validate and normalize a bundled-skill manifest (display metadata only)."""
    if not isinstance(manifest, dict):
        raise BundledSkillError("bundled skill manifest must be an object")
    missing = [f for f in _REQUIRED_FIELDS if not str(manifest.get(f, "")).strip()]
    if missing:
        raise BundledSkillError(
            "bundled skill manifest missing: " + ", ".join(missing))
    name = str(manifest.get("name", "")).strip().lower()
    if not _SKILL_NAME_RE.match(name):
        raise BundledSkillError(
            "bundled skill name must match ^[a-z][a-z0-9._-]{1,63}$")
    description = _check_branded(
        str(manifest.get("description", "")).strip()[:_MAX_DESCRIPTION],
        "description")
    kind = str(manifest.get("kind", "bundled-skill") or "bundled-skill").strip()
    if kind != "bundled-skill":
        raise BundledSkillError("bundled skill kind must be 'bundled-skill'")
    source = str(manifest.get("source", "qubit-bundled")
                 or "qubit-bundled").strip()
    if source != "qubit-bundled":
        raise BundledSkillError("bundled skill source must be 'qubit-bundled'")
    raw_caps = manifest.get("capabilities", []) or []
    if not isinstance(raw_caps, (list, tuple)):
        raise BundledSkillError("bundled skill capabilities must be a list")
    caps: List[str] = []
    for cap in raw_caps[:_MAX_CAPABILITIES]:
        clean = str(cap).strip().lower()
        if not _CAPABILITY_RE.match(clean):
            raise BundledSkillError(
                f"bundled skill capability {cap!r} is not a valid id")
        caps.append(clean)
    digest = str(manifest.get("digest", "")).strip().lower()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise BundledSkillError("bundled skill digest must be sha256 hex")
    out: Dict[str, Any] = {
        "schema": BUNDLED_SKILL_SCHEMA,
        "name": name,
        "description": description,
        "version": str(manifest.get("version", "1.0.0") or "1.0.0")[:32],
        "kind": "bundled-skill",
        "source": "qubit-bundled",
        "capabilities": sorted(set(caps)),
        "branded": True,
    }
    for field in ("path", "digest"):
        if manifest.get(field):
            out[field] = str(manifest[field])
    return out


def load_bundled_skill(name: str) -> Dict[str, Any]:
    """Load, validate, and digest one bundled ``SKILL.md`` (real file I/O)."""
    clean = _check_name(name)
    path = bundled_skills_root() / clean / "SKILL.md"
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BundledSkillError(
            f"bundled skill {clean!r} is missing: {exc}") from exc
    if size > MAX_BUNDLED_MANIFEST_BYTES:
        raise BundledSkillError(
            f"bundled skill {clean!r} exceeds {MAX_BUNDLED_MANIFEST_BYTES} bytes")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundledSkillError(
            f"bundled skill {clean!r} unreadable: {exc}") from exc
    front = parse_skill_front_matter(raw.decode("utf-8", errors="replace"))
    front.setdefault("name", clean)
    front["path"] = f"skills/{clean}/SKILL.md"
    front["digest"] = hashlib.sha256(raw).hexdigest()
    manifest = validate_bundled_skill_manifest(front)
    if manifest["name"] != clean:
        raise BundledSkillError(
            f"bundled skill directory {clean!r} declares name "
            f"{manifest['name']!r}")
    return manifest


def bundled_skill_manifests() -> List[Dict[str, Any]]:
    """Every bundled skill manifest that loads and validates (no network)."""
    return [load_bundled_skill(name) for name in BUNDLED_SKILLS]


def register_bundled_skills(store: Any = None) -> Dict[str, Any]:
    """Validate the bundled skill registry and return its listing.

    ``store`` is accepted for symmetry with the existing registry pattern
    (``VerifiedSkillStore``); the bundled manifests remain read-only and are not
    written into the agent-authored advisory memory. A malformed bundled
    manifest fails the whole call closed so a broken bundle is never listed as
    available.
    """
    manifests = bundled_skill_manifests()
    return {
        "state": "OK",
        "schema": BUNDLED_SKILL_SCHEMA,
        "count": len(manifests),
        "skills": manifests,
        "registered": True,
        "store": type(store).__name__ if store is not None else "read-only",
        "note": "bundled Qubit skills are validated and listed read-only; they "
                "are not agent-authored memory records and never grant a "
                "capability",
    }


def get_bundled_skill(name: str) -> Optional[Dict[str, Any]]:
    """Return one bundled manifest, or ``None`` for an unknown bundled name."""
    if str(name or "").strip().lower() not in BUNDLED_SKILLS:
        return None
    return load_bundled_skill(name)


# Natural aliases used by callers/tests.
bundled_skills = bundled_skill_manifests
load_bundled_skill_manifest = load_bundled_skill
