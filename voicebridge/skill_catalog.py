"""One skills authority for dashboard, CLI, voice, chat, and TUI.

Source-first port of the reference dashboard skills behavior
(``hermes_cli/web_routers/skills.py`` @ db39ee3f: ``get_skills`` lists
``_find_all_skills(skip_disabled=True)`` annotated with ``enabled``,
``usage`` and ``provenance``; ``get_skill_content`` reads the raw SKILL.md
resolved by ``tools.skill_manager_tool._find_skill``; create/update/delete go
through ``_create_skill``/``_edit_skill``/``_delete_skill``; toggle flips the
vendored disabled set) wired to the vendored fork's identical implementation
(``qubit_runtime/tools/skill_manager_tool.py``,
``qubit_runtime/qubit_cli/skills_config.py``) under the Qubit-owned runtime
home, plus the Qubit policy lane:

* reads: permissioned-store manifests are merged in (origin ``permissioned``)
  so the Qubit install-gate lane stays visible everywhere;
* mutations: callers (compat routes, voice registry, CLI) own confirmation
  (durable journal / ``--confirm``) and e-stop; every write here is audited
  and clears the vendored skills prompt cache, mirroring the reference router.

The vendored on-disk catalog resolves through ``QUBIT_HOME`` (set by
``use_qubit_home``), so it never silently reads the external ``~/.hermes``
unless the process genuinely has no Qubit home.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillAuthorityError(Exception):
    """Typed skills-authority failure carrying a stable ``code``."""

    NOT_FOUND = "SKILL_NOT_FOUND"
    INVALID = "SKILL_INVALID"
    AMBIGUOUS = "SKILL_AMBIGUOUS"
    QUARANTINED = "SKILL_QUARANTINED"
    UNREADABLE = "SKILL_UNREADABLE"
    ESSENTIAL = "SKILL_ESSENTIAL"

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fork_scope(cfg: Any) -> None:
    """Put the fork on ``sys.path`` and point it at the Qubit-owned home."""
    from . import qubit_runtime

    qubit_runtime.ensure_path()
    # Never scan the operator's external ~/.hermes: resolve the Qubit-owned
    # runtime home explicitly (idempotent once a non-external QUBIT_HOME is
    # set; a no-op in tests that set QUBIT_HOME to a sandbox).
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - a failed resolution must not hide the store set
        pass


def _audit(action: str, actor: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"skills.{action}", actor=actor, **detail)
    except Exception:  # noqa: BLE001 - audit must never break the mutation
        pass


def _disabled_names() -> set:
    """Vendored disabled set (the same authority the catalog annotates)."""
    try:
        from qubit_cli.skills_config import get_disabled_skills  # type: ignore
        from qubit_cli.config import load_config  # type: ignore

        return set(get_disabled_skills(load_config()) or [])
    except Exception:  # noqa: BLE001
        return set()


def _skill_index(cfg: Any) -> Dict[str, List[Dict[str, Any]]]:
    """One pass over every skill root, keyed by directory basename AND
    frontmatter ``name`` — the same dual lookup the reference
    ``_collect_skill_candidates`` performs.

    The project tier is walked through the quarantine chokepoint
    (``iter_project_skill_files``), the profile/local + external tiers through
    ``iter_skill_index_files`` (which prunes ``EXCLUDED_SKILL_DIRS`` such as
    ``.archive``). ``resolve_skill`` is the single reader over this index so
    catalog/detail/edit/delete/toggle cannot drift apart.
    """
    _fork_scope(cfg)
    from agent.skill_utils import (  # type: ignore
        get_all_skills_dirs,
        get_project_skills_dirs,
        iter_project_skill_files,
        iter_skill_index_files,
        parse_frontmatter,
    )

    try:
        project_dirs = [Path(d) for d in get_project_skills_dirs()]
    except Exception:  # noqa: BLE001 - project tier is optional
        project_dirs = []
    project_set = set(project_dirs)
    roots = list(project_dirs)
    try:
        roots.extend(Path(d) for d in get_all_skills_dirs())
    except Exception:  # noqa: BLE001 - fork unavailable => no on-disk skills
        pass

    index: Dict[str, List[Dict[str, Any]]] = {}
    seen: set = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        iterator = (iter_project_skill_files(root) if root in project_set
                    else iter_skill_index_files(root, "SKILL.md"))
        for skill_md in iterator:
            key = skill_md
            try:
                key = skill_md.resolve()
            except OSError:
                pass
            if key in seen:
                continue
            seen.add(key)
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            frontmatter, _ = parse_frontmatter(text[:8192])
            fm_name = str(frontmatter.get("name") or "").strip()
            cand = {"skill_dir": skill_md.parent, "skill_md": skill_md,
                    "name": fm_name or skill_md.parent.name}
            for lookup in {skill_md.parent.name, fm_name}:
                if lookup:
                    index.setdefault(lookup, []).append(cand)
    return index


def _dedupe_candidates(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe candidates by resolved ``SKILL.md`` (a symlink/copy of one skill
    is still one skill), preserving first-seen precedence order."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for cand in cands:
        key = cand["skill_md"]
        try:
            key = key.resolve()
        except OSError:
            pass
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def has_on_disk_skill(index: Dict[str, List[Dict[str, Any]]],
                      name: str) -> bool:
    """True when ``name`` resolves to at least one ``SKILL.md`` in ``index``."""
    return bool(_dedupe_candidates(index.get(str(name or "").strip(), [])))


def resolve_skill(cfg: Any, name: str, *,
                  index: Optional[Dict[str, List[Dict[str, Any]]]] = None
                  ) -> Optional[Dict[str, Any]]:
    """ONE on-disk skill resolver: directory basename OR frontmatter ``name``.

    Mirrors the reference ``_collect_skill_candidates`` lookup so the name a
    catalog row exposes (its frontmatter ``name``) always resolves back to a
    real ``SKILL.md``, and a categorized relative path (``mlops/saelens``) still
    resolves through the fork's ``_find_skill``. Returns
    ``{"name", "resolved_name", "skill_dir", "skill_md", "origin"}`` for a real
    file, else ``None``. Raises ``SKILL_AMBIGUOUS`` when the name matches more
    than one distinct file (mirrors the fork's collision refusal instead of
    silently shadowing one skill with another).
    """
    clean = str(name or "").strip()
    if not clean:
        return None
    if index is None:
        try:
            index = _skill_index(cfg)
        except Exception:  # noqa: BLE001 - fork unavailable => no on-disk skill
            index = {}
    cands = _dedupe_candidates(index.get(clean, []))
    if len(cands) > 1:
        exact = [c for c in cands if c["skill_dir"].name == clean]
        if len(exact) == 1:
            cands = exact
        else:
            raise SkillAuthorityError(
                SkillAuthorityError.AMBIGUOUS,
                f"skill name {clean!r} matches {len(cands)} skills: "
                + "; ".join(str(c["skill_md"]) for c in cands))
    if cands:
        cand = cands[0]
        return {"name": clean, "resolved_name": cand["name"],
                "skill_dir": cand["skill_dir"], "skill_md": cand["skill_md"],
                "origin": "catalog"}
    # Categorized relative path ("mlops/saelens"): the fork's _find_skill also
    # accepts the bare dir name and this form.
    try:
        _fork_scope(cfg)
        from tools.skill_manager_tool import _find_skill  # type: ignore

        found = _find_skill(clean)
    except Exception:  # noqa: BLE001
        found = None
    if found and found.get("path"):
        skill_dir = Path(str(found["path"]))
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            return {"name": clean, "resolved_name": skill_md.parent.name,
                    "skill_dir": skill_dir, "skill_md": skill_md,
                    "origin": "catalog"}
    return None


def _fork_lookup_name(cfg: Any, resolved: Dict[str, Any]) -> str:
    """Name the vendored ``_find_skill`` accepts for a resolved skill: the
    categorized relative path under the local skills root, else the dir name
    (external dirs only match by basename)."""
    skill_dir = resolved["skill_dir"]
    try:
        from agent.skill_utils import get_skills_dir  # type: ignore

        local = get_skills_dir().resolve()
        resolved_dir = skill_dir.resolve()
        if resolved_dir.is_relative_to(local):
            return resolved_dir.relative_to(local).as_posix()
    except Exception:  # noqa: BLE001
        pass
    return skill_dir.name


def catalog_entries(cfg: Any) -> List[Dict[str, Any]]:
    """Merged skills catalog.

    Ported from the reference ``get_skills``: the vendored on-disk catalog
    is listed with disabled skills INCLUDED (``skip_disabled=True``) and each
    entry annotated with ``enabled`` (membership in the vendored disabled
    set), ``usage``, and ``provenance`` (``hub`` > ``bundled`` > ``agent``,
    where ``agent`` covers agent-authored and local hand-made skills — the
    ones the operator may edit/delete from the UI).

    Every emitted row resolves to a READABLE ``SKILL.md`` on disk through
    :func:`resolve_skill`; a row that cannot open is not a catalog skill. A
    Qubit-installed (permissioned) record is therefore listed only when its
    skill has a real file — a manifest-only install (no ``SKILL.md``) is a
    capability grant, and emitting it produced the phantom row whose detail
    route had nothing to open. Permissioned skills keep ``origin`` =
    ``permissioned`` so a caller can reason about which invocation path
    applies (see ``voicebridge.invocation_matrix``).
    """
    items: Dict[str, Dict[str, Any]] = {}
    try:
        index = _skill_index(cfg)
    except Exception:  # noqa: BLE001 - fork unavailable: serve the store set
        index = {}
    try:
        from .permissions import permissions_store

        for rec in permissions_store(cfg).skills():
            d = rec.to_dict() if hasattr(rec, "to_dict") else {}
            man = d.get("manifest", {}) if isinstance(d, dict) else {}
            name = str(d.get("skill_id", ""))
            if not name or not has_on_disk_skill(index, name):
                continue
            items[name] = {
                "name": name,
                "description": str(man.get("description", "") or d.get("status", "")),
                "category": str(man.get("category", "") or "qubit"),
                # The permissioned store uses ``staged``/``active``/``disabled``
                # (enable_skill sets ``active``); accept the legacy ``enabled``
                # token too so an operator-enabled skill is not mis-reported as
                # disabled on the dashboard/CLI/voice.
                "enabled": str(d.get("status", "")) in ("active", "enabled"),
                "origin": "permissioned",
                "provenance": "qubit",
                "usage": 0,
            }
    except Exception:  # noqa: BLE001 - a permission-store miss must not hide the catalog
        pass
    try:
        _fork_scope(cfg)
        from tools.skills_tool import _find_all_skills  # type: ignore
        from qubit_cli.skills_config import get_disabled_skills  # type: ignore
        from qubit_cli.config import load_config  # type: ignore

        try:
            disabled = set(get_disabled_skills(load_config()) or [])
        except Exception:  # noqa: BLE001
            disabled = set()
        try:
            from tools.skill_usage import (  # type: ignore
                _read_bundled_names,
                _read_hub_installed_names,
                activity_count,
                load_usage,
            )

            bundled_names = set(_read_bundled_names() or [])
            hub_names = set(_read_hub_installed_names() or [])
            usage = load_usage() or {}
        except Exception:  # noqa: BLE001 - usage/provenance are annotations, not the catalog
            bundled_names = set()
            hub_names = set()
            usage = {}

            def activity_count(_record: Any) -> int:
                return 0

        # skip_disabled=True: include disabled skills so the UI can show the
        # real enabled state instead of hiding them (reference behavior).
        for s in _find_all_skills(skip_disabled=True) or []:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "")
            if not name or name in items or not has_on_disk_skill(index, name):
                continue
            items[name] = {
                "name": name,
                "description": str(s.get("description") or ""),
                "category": str(s.get("category") or "bundled"),
                "enabled": name not in disabled,
                "origin": "catalog",
                "provenance": (
                    "hub" if name in hub_names
                    else "bundled" if name in bundled_names
                    else "agent"),
                "usage": int(activity_count(usage.get(name, {})) or 0),
            }
    except Exception:  # noqa: BLE001 - fork catalog unavailable: serve the store set
        pass
    return list(items.values())


def merged_store_view(cfg: Any) -> List[Dict[str, Any]]:
    """Permission-record-shaped view for the CLI/voice ``skills list``.

    Permissioned records whose ``SKILL.md`` resolves on disk are returned
    unchanged; catalog-only skills are appended as
    ``{skill_id, status, manifest, source}`` so the CLI and voice report the
    same catalog the dashboard does, without inventing a permission record for
    a skill the operator never installed.

    A permissioned record with no on-disk ``SKILL.md`` is a capability grant,
    not a model skill: the agent's ``skills_list``/``skill_view`` scan skill
    directories and can never resolve it. Reporting it here made ``qubit
    skills list`` disagree with the agent (the phantom ``qubit-boards`` row),
    so the same on-disk gate ``catalog_entries`` applies is applied here.
    """
    from .permissions import permissions_store

    store = permissions_store(cfg)
    entries = catalog_entries(cfg)
    on_disk = {str(e.get("name", "")) for e in entries}
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    try:
        for rec in store.skills():
            d = rec.to_dict()
            name = str(d.get("skill_id", ""))
            if not name or name not in on_disk:
                continue  # manifest-only capability grant, not a skill
            seen.add(name)
            rows.append(d)
    except Exception:  # noqa: BLE001
        pass
    for entry in entries:
        name = str(entry.get("name", ""))
        if not name or name in seen:
            continue
        rows.append({
            "skill_id": name,
            "status": "enabled" if entry.get("enabled") else "disabled",
            "manifest": {
                "description": entry.get("description", ""),
                "category": entry.get("category", "bundled"),
            },
            "source": "catalog",
        })
    return rows


def find_skill_path(cfg: Any, name: str) -> Optional[Path]:
    """On-disk skill directory for ``name`` via :func:`resolve_skill`.

    Accepts the directory basename, the frontmatter ``name``, and the
    categorized relative path — the forms the vendored ``skill_view`` and the
    catalog both expose. Returns ``None`` when no on-disk skill matches (a
    permissioned-manifest-only skill has no path).
    """
    clean = str(name or "").strip()
    if not clean:
        return None
    try:
        resolved = resolve_skill(cfg, clean)
    except SkillAuthorityError:  # ambiguous: not a single on-disk skill
        return None
    return resolved["skill_dir"] if resolved is not None else None


def read_skill_content(cfg: Any, name: str) -> Optional[Dict[str, Any]]:
    """Raw SKILL.md text for the dashboard editor (live ``get_skill_content``).

    Resolves through :func:`resolve_skill` (dir basename OR frontmatter name),
    so every catalog row opens; a permissioned-manifest-only skill (no on-disk
    file) falls back to its manifest text. Returns ``None`` only when neither
    authority holds the skill.
    """
    clean = str(name or "").strip()
    if not clean:
        return None
    resolved = resolve_skill(cfg, clean)
    if resolved is not None:
        skill_md = resolved["skill_md"]
        if not skill_md.exists():
            raise SkillAuthorityError(
                SkillAuthorityError.UNREADABLE,
                f"Skill {clean!r} has no SKILL.md.")
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillAuthorityError(
                SkillAuthorityError.UNREADABLE,
                f"Skill {clean!r} could not be read: {exc}") from exc
        return {"name": clean, "content": content, "path": str(skill_md),
                "origin": "catalog"}
    try:
        from .permissions import permissions_store

        rec = permissions_store(cfg).skill(clean)
    except Exception:  # noqa: BLE001
        rec = None
    if rec is None:
        return None
    d = rec.to_dict()
    man = d.get("manifest", {}) if isinstance(d, dict) else {}
    content = str(man.get("instructions", "") or man.get("description", "") or "")
    return {"name": clean, "content": content,
            "path": str(d.get("source_path", "") or ""),
            "origin": "permissioned"}


def show_skill(cfg: Any, name: str) -> Optional[Dict[str, Any]]:
    """One skill for CLI/voice ``show``: on-disk metadata first (through
    :func:`resolve_skill`), then the permissioned record. Returns ``None`` when
    neither authority holds it."""
    clean = str(name or "").strip()
    if not clean:
        return None
    resolved = resolve_skill(cfg, clean)
    if resolved is not None:
        entry = next((e for e in catalog_entries(cfg)
                      if str(e.get("name", "")) == clean), None)
        out = dict(entry) if entry is not None else {"name": clean}
        out.setdefault("origin", "catalog")
        out["path"] = str(resolved["skill_md"])
        return out
    try:
        from .permissions import permissions_store

        rec = permissions_store(cfg).skill(clean)
    except Exception:  # noqa: BLE001
        rec = None
    if rec is None:
        return None
    out = rec.to_dict()
    out["origin"] = "permissioned"
    return out


def clear_skills_prompt_cache() -> None:
    """Best-effort skills prompt-cache invalidation after a write (mirrors
    the reference router so a dashboard-authored skill is picked up by the
    next session without a manual cache reset)."""
    try:
        _fork_scope(None)
        from agent.prompt_builder import (  # type: ignore
            clear_skills_system_prompt_cache,
        )

        clear_skills_system_prompt_cache(clear_snapshot=True)
    except Exception:  # noqa: BLE001
        pass


def _fork_result_or_raise(result: Any, name: str) -> Dict[str, Any]:
    """Normalize a fork skill-manager result dict; raise typed on failure."""
    if not isinstance(result, dict):
        raise SkillAuthorityError(
            SkillAuthorityError.INVALID,
            f"Skill operation for {name!r} returned an unexpected result.")
    if result.get("success"):
        return result
    err = str(result.get("error", "") or "Skill operation failed.")
    if "not found" in err.lower():
        raise SkillAuthorityError(SkillAuthorityError.NOT_FOUND, err)
    raise SkillAuthorityError(SkillAuthorityError.INVALID, err)


def create_skill_file(cfg: Any, name: str, content: str,
                      category: Optional[str],
                      *, requested_by: str) -> Dict[str, Any]:
    """Create a skill through the fork's ``_create_skill`` (validation,
    atomic write, security scan). Caller owns confirmation/e-stop."""
    _fork_scope(cfg)
    from tools.skill_manager_tool import _create_skill  # type: ignore

    out = _fork_result_or_raise(
        _create_skill(str(name or ""), str(content or ""),
                      (str(category) if category else None) or None),
        str(name or ""))
    clear_skills_prompt_cache()
    _audit("create", requested_by,
           {"skill_id": str(name or ""), "path": str(out.get("path", ""))})
    return out


def _permissioned_record(cfg: Any, name: str) -> Optional[Dict[str, Any]]:
    """Permissioned-manifest record for ``name`` (the install-gate lane)."""
    try:
        from .permissions import permissions_store

        rec = permissions_store(cfg).skill(name)
    except Exception:  # noqa: BLE001
        return None
    if rec is None:
        return None
    return rec.to_dict()


def edit_skill_file(cfg: Any, name: str, content: str,
                    *, requested_by: str) -> Dict[str, Any]:
    """Full-rewrite a skill's SKILL.md through the fork's skill manager.

    Resolution goes through :func:`resolve_skill`, so a skill whose frontmatter
    ``name`` differs from its directory name edits the real Qubit-owned
    ``SKILL.md``. A permissioned manifest-only skill (record, no on-disk file)
    is materialized first via the fork's ``_create_skill`` with the supplied
    editor content — validated, scanned, and provenance-recorded.
    """
    clean = str(name or "")
    body = str(content or "")
    _fork_scope(cfg)
    from tools.skill_manager_tool import (  # type: ignore
        _create_skill,
        _edit_skill,
    )

    resolved = resolve_skill(cfg, clean)
    materialized = False
    if resolved is not None:
        out = _fork_result_or_raise(
            _edit_skill(_fork_lookup_name(cfg, resolved), body), clean)
    else:
        rec = _permissioned_record(cfg, clean)
        if rec is None:
            raise SkillAuthorityError(
                SkillAuthorityError.NOT_FOUND, f"no skill {clean!r}")
        man = rec.get("manifest", {}) if isinstance(rec, dict) else {}
        category = str(man.get("category", "") or "qubit")
        out = _fork_result_or_raise(
            _create_skill(clean, body, category or None), clean)
        materialized = True
    clear_skills_prompt_cache()
    _audit("edit", requested_by,
           {"skill_id": clean, "path": str(out.get("path", "")),
            "materialized": materialized})
    return out


def delete_skill_file(cfg: Any, name: str,
                      *, requested_by: str) -> Dict[str, Any]:
    """Delete an on-disk skill through the fork's ``_delete_skill`` (curator
    archive guards and pinned-skill guards preserved).

    Resolution goes through :func:`resolve_skill`, so a frontmatter-named skill
    deletes the real directory the catalog row points at.
    """
    _fork_scope(cfg)
    from tools.skill_manager_tool import _delete_skill  # type: ignore

    clean = str(name or "")
    resolved = resolve_skill(cfg, clean)
    if resolved is None:
        raise SkillAuthorityError(
            SkillAuthorityError.NOT_FOUND, f"no skill {clean!r}")
    out = _fork_result_or_raise(
        _delete_skill(_fork_lookup_name(cfg, resolved)), clean)
    clear_skills_prompt_cache()
    _audit("delete", requested_by, {"skill_id": clean})
    return out


def set_skill_enabled(cfg: Any, name: str, enabled: bool,
                      *, requested_by: str,
                      confirmed: bool = False) -> Dict[str, Any]:
    """Enable/disable one skill on every surface (live ``toggle_skill``).

    Resolution goes through :func:`resolve_skill`. Every on-disk skill flips
    the vendored disabled set (``<QUBIT_HOME>/config.yaml`` ``skills.disabled``)
    keyed by its frontmatter name — the same set the catalog annotates and the
    runtime loader enforces. A permissioned-manifest-only record (no on-disk
    file) keeps the permissioned store as its state owner and requires its own
    ``confirmed`` bit (enabling a staged plugin grants authority; the CLI
    passes ``--confirm``, the dashboard/voice paths have already settled the
    shared journal confirmation). Caller owns confirmation/e-stop.
    """
    clean = str(name or "").strip()
    if not clean:
        raise SkillAuthorityError(SkillAuthorityError.INVALID,
                                  "skill name required")
    resolved = resolve_skill(cfg, clean)
    if resolved is None:
        try:
            from .permissions import permissions_store

            store = permissions_store(cfg)
            rec = store.skill(clean)
        except Exception:  # noqa: BLE001
            store = None
            rec = None
        if rec is not None and store is not None:
            if enabled:
                changed = store.enable_skill(clean, requested_by=requested_by,
                                             confirmed=confirmed)
            else:
                changed = store.disable_skill(clean, requested_by=requested_by,
                                              confirmed=confirmed)
            status = str(getattr(changed, "status", "")
                         or ("active" if enabled else "disabled"))
            _audit("toggle", requested_by,
                   {"skill_id": clean, "enabled": bool(enabled),
                    "origin": "permissioned"})
            return {"skill_id": clean, "enabled": bool(enabled),
                    "origin": "permissioned", "status": status}
        # Fail closed: enabling/disabling a skill that exists in neither the
        # on-disk catalog nor the permissioned store is not a real skill. The
        # old fall-through silently wrote a disabled-set entry for any name.
        raise SkillAuthorityError(
            SkillAuthorityError.NOT_FOUND, f"no skill {clean!r}")
    # The vendored disabled set is keyed by the skill's frontmatter name (the
    # name the catalog exposes and the loader matches); fall back to the
    # requested name for a permissioned-only record with no file.
    target = (resolved["resolved_name"] or clean) if resolved else clean
    _fork_scope(cfg)
    try:
        from agent.skill_utils import ESSENTIAL_SKILLS  # type: ignore

        essential = set(ESSENTIAL_SKILLS or [])
    except Exception:  # noqa: BLE001
        essential = set()
    if not enabled and target in essential:
        raise SkillAuthorityError(
            SkillAuthorityError.ESSENTIAL,
            f"Skill {target!r} is essential and cannot be disabled.")
    from qubit_cli.skills_config import (  # type: ignore
        get_disabled_skills,
        save_disabled_skills,
    )
    from qubit_cli.config import load_config, save_config  # type: ignore

    config = load_config()
    disabled = set(get_disabled_skills(config) or [])
    if enabled:
        disabled.discard(target)
    else:
        disabled.add(target)
    save_disabled_skills(config, disabled)
    save_config(config)
    clear_skills_prompt_cache()
    _audit("toggle", requested_by,
           {"skill_id": target, "enabled": bool(enabled), "origin": "catalog"})
    return {"skill_id": target, "enabled": bool(enabled), "origin": "catalog",
            "status": "active" if enabled else "disabled"}


__all__ = ["SkillAuthorityError", "catalog_entries", "merged_store_view",
           "resolve_skill", "find_skill_path", "read_skill_content",
           "show_skill", "clear_skills_prompt_cache", "create_skill_file",
           "edit_skill_file", "delete_skill_file", "set_skill_enabled"]
