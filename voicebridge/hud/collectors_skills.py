"""HUD skills collector — Qubit-native port of ``backend/collectors/skills.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package. Rebranded and re-pointed at
QUBIT state.

What changed from upstream, and why
-----------------------------------
* **Discovery** — upstream did ``skills_dir.rglob("SKILL.md")`` under one
  directory and parsed frontmatter with a hand-rolled ``key: value`` splitter.
  Qubit's skills live across several precedence tiers (the profile's
  ``skills/``, bundled skills, project skills, plugin-registered skills) and are
  already resolved, deduplicated and gated by
  :mod:`tools.skills_tool` + :mod:`agent.skill_utils`. This collector asks that
  authority, so a shadowed duplicate or a disabled skill is reported the way
  ``skill_view`` actually sees it — never as an extra row the agent cannot load.
* **Category** — taken from the resolved ``relative_path`` (so a skill nested
  two directories deep gets its real top-level category) instead of assuming
  ``<category>/<name>/SKILL.md``.
* **``is_custom``** — upstream guessed "custom" from a bulk-mtime heuristic.
  Qubit has real provenance: ``source`` is ``profile`` / ``bundled`` /
  ``external`` from the *resolved* path (a skill symlinked out of the profile
  dir is ``external``, not the profile's own), and ``is_custom`` is true only
  for a skill in the profile's own ``skills/`` dir that the bundled manifest
  does not track.
* **Cache dropped** — upstream cached a 60-second scan. Qubit's catalog is
  already signature-cached by the skills tool, so a second TTL layer would only
  add a way to serve a stale panel.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ensure_vendored() -> None:
    from .. import qubit_runtime

    qubit_runtime.ensure_path()


def _bundled_manifest_names() -> set:
    """Skill names the bundled sync tracks (``skills/.bundled_manifest``).

    Delegates to the bundled-sync module's own reader so the v1 (plain name) and
    v2 (``name:hash``) manifest formats are both understood here exactly as the
    sync tool understands them.
    """
    try:
        from tools.skills_sync import _read_manifest  # type: ignore

        return set(_read_manifest())
    except Exception:  # noqa: BLE001 - an unreadable manifest means "not tracked"
        return set()


def _category(entry: Dict[str, Any]) -> str:
    relative = str(entry.get("relative_path") or "")
    parts = [p for p in relative.split("/") if p]
    if len(parts) >= 2:
        return parts[0]
    return "uncategorized"


def collect_skills(home: Optional[Path] = None, cfg: Any = None) -> Dict[str, Any]:
    """Collect installed skills from Qubit's skill-resolution authority.

    ``cfg`` is the VoiceBridge config (published into ``QUBIT_HOME`` so a
    profile lists its own island's skills).
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "skills": [],
        "total": 0,
        "custom_count": 0,
        "by_category": {},
        "category_counts": {},
        "recently_modified": [],
    }
    try:
        _ensure_vendored()
        from .. import qubit_runtime

        qubit_runtime.use_qubit_home(cfg)
        from tools.skills_tool import _skill_catalog  # type: ignore

        catalog = _skill_catalog(skip_disabled=True) or []
    except Exception as exc:  # noqa: BLE001 - an unreadable catalog is a report
        out["detail"] = f"skill catalog unavailable ({exc})"
        return out

    from qubit_constants import (  # type: ignore
        get_bundled_skills_dir, get_qubit_home)

    profile_home = Path(home) if home is not None else Path(get_qubit_home())
    profile_skills = (profile_home / "skills").resolve(strict=False)
    bundled_root = Path(get_bundled_skills_dir()).resolve(strict=False)
    bundled_names = _bundled_manifest_names()

    skills: List[Dict[str, Any]] = []
    for entry in catalog:
        raw_path = entry.get("path")
        path = Path(raw_path) if raw_path else None
        try:
            stat = path.stat() if path else None
        except OSError:
            stat = None
        resolved = path.resolve(strict=False) if path else None
        in_profile = bool(resolved and profile_skills in resolved.parents)
        in_bundled = bool(resolved and bundled_root in resolved.parents)
        declared = str(entry.get("name") or (path.parent.name if path else ""))
        # The profile's own dir, the inline-shipped bundled tree, or an external
        # tier — the same resolution the loader used, reported for provenance.
        source = ("profile" if in_profile else
                  "bundled" if in_bundled else "external")
        skills.append({
            "name": declared,
            "load_name": str(entry.get("load_name") or declared),
            "category": _category(entry),
            "relative_path": str(entry.get("relative_path") or ""),
            "description": str(entry.get("description") or ""),
            "path": str(path) if path else "",
            "source": source,
            "status": str(entry.get("status") or ""),
            "is_custom": bool(in_profile and declared not in bundled_names),
            "file_size": int(stat.st_size) if stat else 0,
            "modified_at": (datetime.fromtimestamp(stat.st_mtime).isoformat()
                            if stat else None),
        })

    by_category: Dict[str, List[str]] = {}
    for skill in skills:
        by_category.setdefault(skill["category"], []).append(skill["name"])

    recent = sorted((s for s in skills if s["modified_at"]),
                    key=lambda s: s["modified_at"], reverse=True)[:10]

    out.update(
        available=True,
        detail=f"{len(skills)} skill(s) resolved across Qubit's tiers",
        skills=skills,
        total=len(skills),
        custom_count=sum(1 for s in skills if s["is_custom"]),
        by_category=by_category,
        category_counts={k: len(v) for k, v in by_category.items()},
        recently_modified=recent,
    )
    return out


__all__ = ["collect_skills"]
