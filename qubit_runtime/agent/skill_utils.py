"""Lightweight skill metadata utilities shared by prompt_builder and skills_tool.
Import-light by design: no tool registry, CLI config, or provider resolution."""

import ast
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from qubit_constants import get_config_path, get_skills_dir, is_termux

logger = logging.getLogger(__name__)

PLATFORM_MAP = {"macos": "darwin", "linux": "linux", "windows": "win32"}

EXCLUDED_SKILL_DIRS = frozenset((
    ".git", ".github", ".hub", ".archive", ".curator_backups",
    ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
))

# Progressive-disclosure support dirs inside a skill package: loaded explicitly
# via skill_view(skill, file_path=...), never scanned as standalone skills.
SKILL_SUPPORT_DIRS = frozenset(("references", "templates", "assets", "scripts"))

# Org mirrors live under skills/_org/<org_id>/ and are TOKEN-GATED: the sync
# client writes the marker after verifying the token; no marker => no org skills
# load. The marker persists offline so already-pulled org skills keep working.
ORG_MIRROR_DIR_NAME = "_org"
ORG_ACTIVE_MARKER = ".active_org"
ORG_PROVENANCE_FILE = ".org-provenance.json"
ORG_BASELINE_FILE = ".org-baseline.json"  # upstream fingerprint; detects local edits


def read_active_org_id(skills_dir: Path) -> Optional[str]:
    """The org id whose mirror may resolve, or None (no org skills load)."""
    marker = skills_dir / ORG_MIRROR_DIR_NAME / ORG_ACTIVE_MARKER
    try:
        return (marker.read_text(encoding="utf-8").strip() or None) if marker.exists() else None
    except OSError:
        return None


def _org_rel_parts(path, skills_dir: Path) -> Tuple[str, ...]:
    """Path parts of *path* relative to *skills_dir* if it is under ``_org/``, else ``()``."""
    try:
        parts = Path(path).resolve().relative_to(Path(skills_dir).resolve()).parts
    except (OSError, ValueError):
        return ()
    return parts if parts and parts[0] == ORG_MIRROR_DIR_NAME else ()


def is_org_mirror_path(path, skills_dir: Path) -> bool:
    """True when *path* is inside the org mirror (``_org/``)."""
    return bool(_org_rel_parts(path, skills_dir))


def org_id_of_path(path, skills_dir: Path) -> Optional[str]:
    """The ``<org_id>`` segment for a path under ``_org/<org_id>/...``."""
    parts = _org_rel_parts(path, skills_dir)
    return parts[1] if len(parts) >= 2 else None


def is_excluded_skill_path(path, *, root: Optional[Path] = None) -> bool:
    """True if *path* should be skipped by skill scanners (VCS/dependency/cache
    dirs + support packages). Apply to every SKILL.md from a direct ``rglob``."""
    parts = PurePath(str(path)).parts
    return any(part in EXCLUDED_SKILL_DIRS for part in parts) or is_skill_support_path(path, root=root)


def is_skill_support_path(path, *, root: Optional[Path] = None) -> bool:
    """True if *path* is under a support dir sitting directly inside a skill root
    (``skills/scripts/foo`` stays discoverable: no ``SKILL.md`` above ``scripts``)."""
    path_obj = path if isinstance(path, Path) else Path(str(path))
    parts = path_obj.parts
    base = root if root is not None and not path_obj.is_absolute() else Path()
    # Only components before the leaf can be containing support directories.
    return any(
        part in SKILL_SUPPORT_DIRS and (base / Path(*parts[:idx]) / "SKILL.md").exists()
        for idx, part in enumerate(parts[:-1])
        if idx > 0
    )


_yaml_load_fn = None


def yaml_load(content: str):
    """Parse YAML with lazy import and CSafeLoader preference."""
    global _yaml_load_fn
    if _yaml_load_fn is None:
        import functools
        import yaml
        _yaml_load_fn = functools.partial(yaml.load, Loader=getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader)
    return _yaml_load_fn(content)


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from markdown; returns (frontmatter_dict, body).
    Malformed YAML falls back to key:value line splitting. A leading UTF-8 BOM
    (Windows editors) is stripped first or it would defeat the ``---`` fence check."""
    content = content.removeprefix("\ufeff")
    end_match = re.search(r"\n---\s*\n", content[3:]) if content.startswith("---") else None
    if not end_match:
        return {}, content
    yaml_content = content[3 : end_match.start() + 3]
    body = content[end_match.end() + 3 :]
    frontmatter: Dict[str, Any] = {}
    try:
        parsed = yaml_load(yaml_content)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except Exception:
        for line in yaml_content.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()
    return frontmatter, body


def skill_matches_platform_list(platforms: Any) -> bool:
    """Return True when *platforms* is compatible with the current OS."""
    if not platforms:
        return True
    running_in_termux = is_termux()
    for platform in platforms if isinstance(platforms, list) else [platforms]:
        normalized = str(platform).lower().strip()
        mapped = PLATFORM_MAP.get(normalized, normalized)
        # Termux is a Linux userland on Android: accept linux-tagged skills
        # whether sys.platform is "linux" (pre-3.13) or "android" (3.13+).
        if sys.platform.startswith(mapped) or (running_in_termux and mapped in ("linux", "termux", "android")):
            return True
    return False


def skill_matches_platform(frontmatter: Dict[str, Any]) -> bool:
    """True when the skill's ``platforms:`` list (absent = all) matches this OS."""
    return skill_matches_platform_list(frontmatter.get("platforms"))


# An ``environments:`` tag is a *relevance* gate for offer surfaces (index,
# autocomplete, slash commands), not a compatibility gate: an explicit load
# (skill_view, --skills) always succeeds. Detection is cached per process.

_ENV_DETECT_CACHE: Dict[str, bool] = {}


def _detect_kanban() -> bool:
    # Mirror tools/kanban_tools.py: a dispatcher-spawned worker (env vars, but
    # only when this execution OWNS the task — delegate children / in-process
    # cron see the worker's vars) or a profile opted into the kanban toolset.
    if os.getenv("HERMES_KANBAN_TASK") or os.getenv("HERMES_KANBAN_BOARD"):
        try:
            from agent.delegation_context import is_dispatcher_owned_worker_context
            owned = is_dispatcher_owned_worker_context()
        except Exception:
            owned = True
        if owned:
            return True
    try:
        from tools.kanban_tools import _profile_has_kanban_toolset
        return bool(_profile_has_kanban_toolset())
    except Exception:
        return False


def _detect_docker() -> bool:
    try:
        from qubit_constants import is_container
        return is_container()
    except Exception:
        return False


_ENV_DETECTORS: Dict[str, Callable[[], bool]] = {
    "kanban": _detect_kanban, "docker": _detect_docker,
    "s6": lambda: os.path.isdir("/run/s6") or os.path.isdir("/package/admin/s6-overlay"),  # s6-overlay is PID 1 in the image
}


def _detect_environment(env: str) -> bool:
    """True when the named runtime environment is active (unknown => True).
    Cached per process EXCEPT ``kanban``: that verdict is context-dependent
    (delegate children / in-process cron see the worker's vars), so a
    process-wide cache would leak the first asker's answer to the others."""
    if env != "kanban" and env in _ENV_DETECT_CACHE:
        return _ENV_DETECT_CACHE[env]
    detector = _ENV_DETECTORS.get(env)
    result = detector() if detector else True
    _ENV_DETECT_CACHE[env] = result
    return result


def skill_matches_environment(frontmatter: Dict[str, Any]) -> bool:
    """True when ANY declared ``environments:`` tag is active (absent = all;
    unknown tags fail open). Offer-time filter only."""
    environments = frontmatter.get("environments")
    if not environments:
        return True
    tags = [str(env).lower().strip() for env in (environments if isinstance(environments, list) else [environments])]
    return any(_detect_environment(tag) for tag in tags if tag)


_RAW_CONFIG_CACHE: Dict[Tuple[str, int, int], Dict[str, Any]] = {}


def _raw_config_cache_clear() -> None:
    """Test hook — drop the shared raw config cache."""
    _RAW_CONFIG_CACHE.clear()


def _config_cache_key(config_path: Path) -> Optional[Tuple[str, int, int]]:
    """``(path, mtime_ns, size)`` identity of config.yaml, or None when unreadable/absent."""
    try:
        stat = config_path.stat()
        return (str(config_path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def _load_raw_config() -> Dict[str, Any]:
    """Read config.yaml with an mtime+size keyed cache (no qubit_cli.config import)."""
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    cache_key = _config_cache_key(config_path)
    cached = _RAW_CONFIG_CACHE.get(cache_key) if cache_key is not None else None
    if cached is not None:
        return cached
    try:
        parsed = yaml_load(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("Could not read skill config %s: %s", config_path, e)
        return {}
    if not isinstance(parsed, dict):
        return {}
    if cache_key is not None:
        _RAW_CONFIG_CACHE.clear()
        _RAW_CONFIG_CACHE[cache_key] = parsed
    return parsed


def _skills_cfg() -> Optional[Dict[str, Any]]:
    """The ``skills:`` mapping from config.yaml, or None when absent/malformed."""
    skills_cfg = _load_raw_config().get("skills")
    return skills_cfg if isinstance(skills_cfg, dict) else None


def _skills_cfg_get(key: str) -> Any:
    """``skills.<key>`` from config.yaml, or None when the section is absent/malformed."""
    skills_cfg = _skills_cfg()
    return skills_cfg.get(key) if skills_cfg is not None else None


def _expand_path(entry: str) -> Path:
    """Expand ``~`` and ``${VAR}`` in a config path entry."""
    return Path(os.path.expanduser(os.path.expandvars(entry)))


def _home_relative(p: Path) -> Path:
    """Anchor a relative config path at QUBIT_HOME; absolute paths pass through."""
    from qubit_constants import get_qubit_home
    return p if p.is_absolute() else get_qubit_home() / p


# Never disableable: `qubit-agent` is the agent's own operating manual and the
# system prompt points at it unconditionally. (Renamed from `hermes-agent` by the
# W-BRAND skills migration; see LEGACY_SKILL_NAME_RENAMES below.)
ESSENTIAL_SKILLS: frozenset = frozenset({"qubit-agent"})


def get_disabled_skill_names(platform: str | None = None) -> Set[str]:
    """Disabled skill names from config.yaml: global list ∪ platform list
    (*platform* defaults to ``HERMES_PLATFORM`` / ``HERMES_SESSION_PLATFORM``)."""
    skills_cfg = _skills_cfg()
    if skills_cfg is None:
        return set()
    from gateway.session_context import get_session_env
    resolved_platform = platform or os.getenv("HERMES_PLATFORM") or get_session_env("HERMES_SESSION_PLATFORM")
    disabled = _normalize_string_set(skills_cfg.get("disabled"))
    platform_disabled = (skills_cfg.get("platform_disabled") or {}).get(resolved_platform) if resolved_platform else None
    if platform_disabled is not None:
        disabled |= _normalize_string_set(platform_disabled)
    return disabled - ESSENTIAL_SKILLS


def parse_config_string_list(value) -> List[str]:
    """Normalize a config value that may hold a JSON-array string into a list.
    ``qubit config set`` stores lists as quoted JSON/Python-literal strings;
    treating one as a single name would silently filter nothing. A scalar
    string still means one name.

    See #13026, #86661.
    """
    if isinstance(value, str):
        if value.strip().startswith("["):
            try:
                parsed = ast.literal_eval(value.strip())
            except (ValueError, SyntaxError):
                parsed = None
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        return [value]
    return [str(item) for item in value] if isinstance(value, (list, tuple, set, frozenset)) else []


def _normalize_string_set(values) -> Set[str]:
    return {name.strip() for name in parse_config_string_list(values) if name.strip()}


# config identity -> resolved external dirs. Called once per skill during
# banner / tool-registry scans; re-resolving each time dominated cold-start.
_EXTERNAL_DIRS_CACHE: Dict[Tuple[str, int], List[Path]] = {}


def _external_dirs_cache_clear() -> None:
    """Test hook — drop the in-process cache."""
    _EXTERNAL_DIRS_CACHE.clear()
    _raw_config_cache_clear()


def _config_str_list(raw) -> List[str]:
    """A scalar-or-list config entry as a list of stripped non-empty strings."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [e for e in (str(entry).strip() for entry in raw) if e]


def get_external_skills_dirs() -> List[Path]:
    """Validated, deduplicated ``skills.external_dirs`` (existing dirs only). Entries
    are ``~``/``${VAR}`` expanded, relative to QUBIT_HOME; the local skills dir is skipped."""
    config_path = get_config_path()
    if not config_path.exists():
        return []
    full_key = _config_cache_key(config_path)
    cache_key = full_key[:2] if full_key is not None else None
    cached = _EXTERNAL_DIRS_CACHE.get(cache_key) if cache_key is not None else None
    if cached is not None:
        return list(cached)  # copy so callers can't mutate the cache
    skills_cfg = _skills_cfg()
    if skills_cfg is None:
        return []
    local_skills = get_skills_dir().resolve()
    result: List[Path] = []
    for entry in _config_str_list(skills_cfg.get("external_dirs")):
        p = _home_relative(_expand_path(entry)).resolve()
        if p == local_skills or p in result:
            continue
        if p.is_dir():
            result.append(p)
        else:
            logger.debug("External skills dir does not exist, skipping: %s", p)
    if cache_key is not None:
        _EXTERNAL_DIRS_CACHE[cache_key] = list(result)
    return result


def get_skill_create_dir() -> Optional[Path]:
    """Configured ``skills.create_dir`` (need not exist yet), or None when unset;
    relative to QUBIT_HOME; a value equal to the local skills dir counts as unset."""
    raw = _skills_cfg_get("create_dir")
    entry = str(raw).strip() if raw and isinstance(raw, (str, os.PathLike)) else ""
    if not entry:
        return None
    p = _home_relative(_expand_path(entry))
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    try:
        if resolved == get_skills_dir().resolve():
            return None
    except OSError:
        pass
    return resolved


def display_skill_create_dir() -> str:
    """User-facing path where new skills are created (``~/`` shorthand when
    possible); tool schema descriptions and prompts follow ``skills.create_dir``."""
    from qubit_constants import display_qubit_home
    create_dir = get_skill_create_dir()
    if create_dir is None:
        return f"{display_qubit_home()}/skills/"
    if create_dir.is_relative_to(Path.home()):
        return "~/" + create_dir.relative_to(Path.home()).as_posix() + "/"
    return create_dir.as_posix() + "/"


def get_all_skills_dirs() -> List[Path]:
    """Skill dirs: the Qubit home ``skills/`` first, then create_dir, then external.
    Trusted project dirs are NOT included (higher precedence; see get_project_skills_dirs)."""
    dirs = [get_skills_dir()]
    create_dir = get_skill_create_dir()
    if create_dir is not None and create_dir.is_dir():
        dirs.append(create_dir)
    dirs.extend(d for d in get_external_skills_dirs() if d not in dirs)
    return dirs


# Project-local skills (<root>/.hermes/skills, <root>/.agents/skills; root = nearest
# .git ancestor) are a prompt-injection vector if auto-sourced from any clone, so
# they load only when the root is in ``skills.trusted_project_dirs``; then they
# override same-named profile/bundled skills. cwd + trust list are session-fixed
# so the skills index stays byte-stable.

PROJECT_SKILLS_SUBDIRS = (os.path.join(".hermes", "skills"), os.path.join(".agents", "skills"))

_PROJECT_ROOT_MAX_DEPTH = 64  # walk-up bound for pathological cwds


def find_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Nearest ancestor containing ``.git`` (dir or worktree file), or None.
    Without *start*, the surface's ``TERMINAL_CWD`` wins over process cwd so
    cron/API surfaces inherit an interactive trust decision by project identity.

    When *start* is not given, the surface's working directory wins over the process cwd: ``TERMINAL_CWD``
    is the same per-surface workdir the terminal tool and cron jobs use (a cron job sets it from its per-job
    ``workdir`` without chdir'ing the scheduler process). This is what lets non-interactive surfaces inherit
    a prior interactive trust decision by project identity — and a surface with no workdir in a trusted repo
    simply resolves no project and loads nothing (#48975).
    """
    try:
        if start is None:
            from agent.runtime_cwd import scope_terminal_cwd
            env_cwd = scope_terminal_cwd()
            start = Path(env_cwd) if env_cwd else Path.cwd()
        cur = Path(start).resolve()
    except OSError:
        return None
    home = Path.home().resolve()
    try:
        for _ in range(_PROJECT_ROOT_MAX_DEPTH):
            if (cur / ".git").exists():
                # A dotfiles checkout AT home would make every session
                # project-scoped; treat home itself as non-project.
                return None if cur == home else cur
            if cur.parent == cur:
                return None
            cur = cur.parent
    except OSError:
        pass
    return None


def _project_trusted_dirs_from_config() -> Set[Path]:
    """Resolved set of trusted project roots from ``skills.trusted_project_dirs``."""
    result: Set[Path] = set()
    for entry in _config_str_list(_skills_cfg_get("trusted_project_dirs")):
        try:
            result.add(_expand_path(entry).resolve())
        except OSError:
            continue
    return result


def is_project_root_trusted(root: Path) -> bool:
    """True when *root* is listed in ``skills.trusted_project_dirs``."""
    try:
        return Path(root).resolve() in _project_trusted_dirs_from_config()
    except OSError:
        return False


def _candidate_project_skills_dirs(root: Path) -> List[Path]:
    """Existing skill dirs under *root*, excluding the profile's own skills dir
    (QUBIT_HOME itself may live inside a git checkout)."""
    local_skills = get_skills_dir().resolve()
    dirs: List[Path] = []
    for cand in (root / sub for sub in PROJECT_SKILLS_SUBDIRS):
        try:
            if cand.is_dir() and cand.resolve() != local_skills:
                dirs.append(cand.resolve())
        except OSError:
            continue
    return dirs


def _current_project_root(trusted: bool) -> Optional[Path]:
    """cwd's project root when discovery is on and its trust state == *trusted*."""
    if _skills_cfg_get("project_discovery") is False:
        return None
    root = find_project_root()
    return root if root is not None and is_project_root_trusted(root) == trusted else None


def get_project_skills_dirs() -> List[Path]:
    """Trusted project-local skill dirs for the current cwd (may be empty)."""
    root = _current_project_root(trusted=True)
    return _candidate_project_skills_dirs(root) if root is not None else []


def get_untrusted_project_skills_root() -> Optional[Tuple[Path, int]]:
    """(root, skill_count) when cwd's project has skills but is NOT trusted, else None."""
    root = _current_project_root(trusted=False)
    count = 0
    for d in _candidate_project_skills_dirs(root) if root is not None else ():
        try:
            count += sum(1 for _ in iter_skill_index_files(d, "SKILL.md"))
        except OSError:
            continue
    return (root, count) if count else None


# Scan-time injection defense: trust is a repo-level decision made once, but a
# `git pull` could inject a malicious skill into an already-trusted repo. Every
# project SKILL.md is scanned with the hub's skills_guard scanner (content-hash
# cached under QUBIT_HOME, never inside the repo); "dangerous" excludes the
# skill from index, list, view and slash commands ("caution" loads, as on the hub).

# ── Project skill quarantine (scan-time injection defense) ──────────────── Trust (`qubit skills trust`)
# is a REPO-level decision made once; the repo's skill content keeps changing underneath it with every pull.
# The hub install path runs skills_guard on install, but project skills are read straight from a checkout —
# without this gate a `git pull` could inject a malicious skill into an already-trusted repo with no scan
# anywhere (#48974). Every project SKILL.md's parent dir is scanned with the same skills_guard scanner the
# hub uses (content-hash cached, so the cost is one scan per skill per content change). A "dangerous"
# verdict quarantines the skill: it is excluded from the index, skills_list, skill_view, and slash commands.
# "caution" loads (matches hub behavior for prose-level keyword hits) — the quarantine is for
# high-confidence findings only. The scan cache lives under QUBIT_HOME, never inside the repo (we don't
# write artifacts into the user's checkout).
_PROJECT_SCAN_SOURCE = "project-local"
_PROJECT_QUARANTINE_CACHE: Dict[str, bool] = {}  # skill_dir -> quarantined


def is_quarantined_project_skill(skill_md) -> bool:
    """True when a project skill's scan verdict is ``dangerous``. Fail-closed: a
    scanner crash or missing scanner quarantines the skill. Scans
    unconditionally — non-project callers should not call this."""
    skill_dir = Path(skill_md).parent
    try:
        key = str(skill_dir.resolve())
    except OSError:
        key = str(skill_dir)
    if key in _PROJECT_QUARANTINE_CACHE:
        return _PROJECT_QUARANTINE_CACHE[key]
    try:
        from tools.skills_guard import scan_skill_cached
        from qubit_constants import get_qubit_home
        cache_dir = get_qubit_home() / "cache" / "project_skill_scans"
        result, _prov = scan_skill_cached(skill_dir, source=_PROJECT_SCAN_SOURCE, cache_dir=cache_dir)
        quarantined = result.verdict == "dangerous"
        if quarantined:
            logger.warning("Project skill quarantined (verdict=dangerous): %s — %s", skill_dir, result.summary)
    except Exception:
        logger.warning("Project skill scan failed — quarantining (fail closed): %s", skill_dir, exc_info=True)
        quarantined = True
    _PROJECT_QUARANTINE_CACHE[key] = quarantined
    return quarantined


def iter_project_skill_files(project_dir: Path):
    """Yield non-quarantined SKILL.md files under a trusted project dir — the
    single iteration chokepoint for the project tier, so the quarantine cannot
    be bypassed by a call site forgetting the check."""
    yield from (p for p in iter_skill_index_files(project_dir, "SKILL.md") if not is_quarantined_project_skill(p))


def normalize_skill_lookup_name(identifier: str) -> str:
    """Translate a trusted absolute skill path (slash commands / cron may store
    them) to the relative form ``skill_view()`` accepts."""
    raw_identifier = (identifier or "").strip()
    if not raw_identifier:
        return raw_identifier
    identifier_path = Path(raw_identifier).expanduser()
    if not identifier_path.is_absolute():
        # Read-old: a legacy skill name (e.g. `hermes-agent`) resolves to its
        # Qubit name after the W-BRAND rename migration.
        return resolve_legacy_skill_name(raw_identifier.lstrip("/"))
    # Resolve the primary root via tools.skills_tool at CALL time: tests patch
    # ``tools.skills_tool.SKILLS_DIR`` and skill_view() enforces ``_skills_dir()``
    # (which follows the live profile-scoped QUBIT_HOME), so normalization
    # must agree with that exact root. Import deferred (cycle).
    try:
        # See #67277.
        from tools import skills_tool as _skills_tool
        primary_root = _skills_tool._skills_dir()
    except Exception:
        primary_root = get_skills_dir()
    trusted_roots = [primary_root]
    for getter in (get_project_skills_dirs, get_external_skills_dirs):
        try:
            trusted_roots.extend(getter())
        except Exception:
            pass
    # Prefer the lexical path under a trusted root before resolving symlinks:
    # a Qubit home ``skills/<name>`` may be a symlink to a checkout elsewhere, and
    # resolving first would turn that trusted path into one skill_view rejects.
    for root in trusted_roots:
        if identifier_path.is_relative_to(root):
            return str(identifier_path.relative_to(root))
    try:
        return str(identifier_path.resolve().relative_to(primary_root.resolve()))
    except Exception:
        logger.debug("Skill identifier %r is an absolute path outside trusted skills "
                     "roots — passing through unchanged (skill_view will reject it)", raw_identifier)
        return raw_identifier


def _resolve_for_skill_ownership(path) -> Path:
    path_obj = path if isinstance(path, Path) else Path(str(path))
    try:
        return path_obj.expanduser().resolve()
    except (OSError, RuntimeError):
        return path_obj.expanduser().absolute()


def is_external_skill_path(path) -> bool:
    """True when ``path`` lives under an external or trusted project skills dir.
    Those are externally owned: autonomous lifecycle maintenance treats them as
    read-only (user-directed tool calls may still edit them)."""
    candidate = _resolve_for_skill_ownership(path)
    roots: List[Path] = list(get_external_skills_dirs())
    try:
        roots.extend(get_project_skills_dirs())
    except Exception:
        pass
    return any(candidate.is_relative_to(_resolve_for_skill_ownership(root)) for root in roots)


def _qubit_metadata(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    """``metadata.hermes`` mapping from frontmatter, or ``{}`` when malformed."""
    metadata = frontmatter.get("metadata")
    qubit = metadata.get("hermes") if isinstance(metadata, dict) else None
    return qubit if isinstance(qubit, dict) else {}


# ``session_platforms`` is the gateway-channel gate: session platforms the skill
# is FOR (hidden from the index elsewhere), unlike ``platforms:`` (host OS).
_CONDITION_KEYS = ("fallback_for_toolsets", "requires_toolsets", "fallback_for_tools", "requires_tools", "session_platforms")


def extract_skill_conditions(frontmatter: Dict[str, Any]) -> Dict[str, List]:
    """Extract conditional activation fields from parsed frontmatter (absent = ``[]``)."""
    qubit = _qubit_metadata(frontmatter)
    return {key: qubit.get(key, []) for key in _CONDITION_KEYS}


def extract_skill_config_vars(frontmatter: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ``metadata.hermes.config`` declarations (key/description/default/prompt).
    Entries missing ``key`` or ``description`` are skipped; ``prompt`` defaults to the description."""
    raw = _qubit_metadata(frontmatter).get("config")
    if isinstance(raw, dict):
        raw = [raw]
    if not raw or not isinstance(raw, list):
        return []
    result: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        desc = str(item.get("description", "")).strip()
        if not key or key in result or not desc:
            continue
        entry: Dict[str, Any] = {"key": key, "description": desc}
        if item.get("default") is not None:
            entry["default"] = item["default"]
        prompt_text = item.get("prompt")
        entry["prompt"] = prompt_text.strip() if isinstance(prompt_text, str) and prompt_text.strip() else desc
        result[key] = entry
    return list(result.values())


def discover_all_skill_config_vars() -> List[Dict[str, Any]]:
    """Config var declarations across all enabled, platform-compatible skills,
    deduplicated by key; each dict carries a ``skill`` attribution key."""
    all_vars: Dict[str, Dict[str, Any]] = {}
    disabled = get_disabled_skill_names()
    for skills_dir in get_all_skills_dirs():
        if not skills_dir.is_dir():
            continue
        for skill_file in iter_skill_index_files(skills_dir, "SKILL.md"):
            try:
                frontmatter, _ = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            skill_name = str(frontmatter.get("name") or skill_file.parent.name)
            if skill_name in disabled or not skill_matches_platform(frontmatter):
                continue
            for var in extract_skill_config_vars(frontmatter):
                if var["key"] not in all_vars:
                    var["skill"] = skill_name
                    all_vars[var["key"]] = var
    return list(all_vars.values())


# Skill config vars are stored under skills.config.<logical key> in config.yaml.
SKILL_CONFIG_PREFIX = "skills.config"


def _resolve_dotpath(config: Dict[str, Any], dotted_key: str):
    """Walk a nested dict following a dotted key; None if any part is missing."""
    current = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_skill_config_values(config_vars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Map logical skill config keys to current values (or declared defaults);
    path-like string values are ``~``/``${VAR}`` expanded."""
    config = _load_raw_config()
    resolved: Dict[str, Any] = {}
    for var in config_vars:
        value = _resolve_dotpath(config, f"{SKILL_CONFIG_PREFIX}.{var['key']}")
        if value is None or (isinstance(value, str) and not value.strip()):
            value = var.get("default", "")
        if isinstance(value, str) and ("~" in value or "${" in value):
            value = os.path.expanduser(os.path.expandvars(value))
        resolved[var["key"]] = value
    return resolved


SKILL_PROMPT_DESC_LIMIT = 60


def _normalize_skill_description(frontmatter: Dict[str, Any]) -> str:
    """Normalize a skill's description field for comparison/truncation."""
    raw_desc = frontmatter.get("description", "")
    return str(raw_desc).strip().strip("'\"") if raw_desc else ""


def extract_skill_description(frontmatter: Dict[str, Any]) -> str:
    """Extract a system-prompt-length description from parsed frontmatter."""
    desc = _normalize_skill_description(frontmatter)
    return desc[:SKILL_PROMPT_DESC_LIMIT - 3] + "..." if len(desc) > SKILL_PROMPT_DESC_LIMIT else desc


def is_skill_description_truncated_for_prompt(frontmatter: Dict[str, Any]) -> bool:
    """True when the description will be truncated in the system prompt skill index."""
    return len(_normalize_skill_description(frontmatter)) > SKILL_PROMPT_DESC_LIMIT


def iter_skill_index_files(skills_dir: Path, filename: str):
    """Walk skills_dir yielding sorted paths matching *filename*; prunes
    EXCLUDED_SKILL_DIRS and support dirs of skill roots. Org mirrors are
    TOKEN-GATED: only the active org's subdir is walked, so leaving an org
    stops its skills resolving without manual cleanup."""
    skills_dir_str = str(skills_dir)
    active_org = read_active_org_id(skills_dir)
    org_root = os.path.join(skills_dir_str, ORG_MIRROR_DIR_NAME)
    matches: list[str] = []
    for root, dirs, files in os.walk(skills_dir_str, followlinks=True):
        has_skill_md = "SKILL.md" in files
        if root == skills_dir_str and ORG_MIRROR_DIR_NAME in dirs and active_org is None:
            dirs.remove(ORG_MIRROR_DIR_NAME)
        elif root == org_root:
            dirs[:] = [d for d in dirs if d == active_org]
        dirs[:] = [d for d in dirs if d not in EXCLUDED_SKILL_DIRS and not (has_skill_md and d in SKILL_SUPPORT_DIRS)]
        if filename in files:
            matches.append(os.path.join(root, filename))
    yield from map(Path, sorted(matches))


# Namespace helpers for plugin-provided skills.
_NAMESPACE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def parse_qualified_name(name: str) -> Tuple[Optional[str], str]:
    """Split ``'namespace:skill-name'`` into ``(namespace, bare_name)``; ``(None, name)`` without ``':'``."""
    namespace, sep, bare = name.partition(":")
    return (namespace, bare) if sep else (None, name)


def is_valid_namespace(candidate: Optional[str]) -> bool:
    """Check whether *candidate* is a valid namespace (``[a-zA-Z0-9_-]+``)."""
    return bool(candidate) and bool(_NAMESPACE_RE.match(candidate))


# ---------------------------------------------------------------------------
# Legacy skill brand de-brand + rename migration (W-BRAND skills lane)
# ---------------------------------------------------------------------------
#: Old bundled/agent-created skill directory names -> Qubit names. The rename is
#: a non-destructive read-old/write-new migration: :func:`resolve_legacy_skill_name`
#: maps the old lookup name to the new one, and :func:`migrate_legacy_skill_dirs`
#: moves the data (dir + frontmatter name + usage/manifest keys) without deleting
#: it. :func:`debrand_skill_text` removes the remaining upstream brand from skill
#: prose while preserving wire/provenance/legal identifiers.
LEGACY_SKILL_NAME_RENAMES: Dict[str, str] = {
    "hermes-agent": "qubit-agent",
    "hermes-agent-skill-authoring": "qubit-agent-skill-authoring",
    "inspecting-hermes-desktop-dom": "inspecting-qubit-desktop-dom",
    "hermes-voice-mode": "qubit-voice-mode",
    "hermes-administration": "qubit-administration",
    "openclaw-voice-bridge": "qubit-voice-bridge",
    "openclaw-operations": "qubit-gateway-operations",
    "openclaw-migration": "qubit-migration",
    "hermes-s6-container-supervision": "qubit-s6-container-supervision",
}

#: Reverse map (Qubit name -> legacy name) for read-old fallback.
LEGACY_SKILL_NAME_ALIASES: Dict[str, str] = {
    new: old for old, new in LEGACY_SKILL_NAME_RENAMES.items()}


def resolve_legacy_skill_name(name: str) -> str:
    """Map a legacy skill name to its Qubit name (identity when not renamed)."""
    return LEGACY_SKILL_NAME_RENAMES.get((name or "").strip(), name)


# --- content de-brand (ported/adapted from scripts/qubit_debrand.py) ---------
_DEBRAND_PROSE_WORDS = {"hermes": "qubit", "nous": "qubit", "openclaw": "qubit"}
_DEBRAND_PHRASES = (
    ("Nous Research", "Qubit Labs"),
    ("nous research", "qubit labs"),
    ("NOUS RESEARCH", "QUBIT LABS"),
    ("Nous Portal", "Qubit Labs"),
    ("nous portal", "qubit labs"),
    ("NOUS PORTAL", "QUBIT LABS"),
)
#: Exact compatibility/legal identifiers that survive verbatim.
_DEBRAND_PRESERVE_STRINGS = (
    "__HERMES_BASE_PATH__", "__HERMES_SESSION_TOKEN__",
    "__HERMES_AUTH_REQUIRED__", "__HERMES_PLUGINS__",
    "__HERMES_PLUGIN_SDK__", "X-Hermes-Session-Token",
    "hermes_flow", "hermes.tokenReloadAttempted",
    "hermes-kanban", "hermes-flow", "hermes_achievements",
    "hermes-achievements", "HERMES_QWEN_", "qubit-hermes",
)
_DEBRAND_PROTECT_SPAN = re.compile(
    r"https?://[^\s\"'<>)\]]+"           # URLs
    r"|git@[^\s\"']+"                     # git remotes
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"    # e-mail
    r"|@[A-Za-z0-9_]+"                    # handles
)
_DEBRAND_WIRE_DOTTED = re.compile(
    r"\b(?:hermes|Hermes|HERMES|nous|Nous|NOUS|openclaw|OpenClaw|OPENCLAW)"
    r"[A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)+")
_DEBRAND_WIRE_HYPHEN = re.compile(
    r"\b(?:hermes|openclaw|nous)-[a-z0-9][a-z0-9_-]*")
_DEBRAND_QUOTED_ID = re.compile(r"""["'](?:hermes|openclaw|nous)["']""")
#: External product homes (``.hermes``/``.openclaw``) are load-bearing paths,
#: not prose: preserve them so a migration/compat reference is never rewritten.
_DEBRAND_DOT_HOME = re.compile(r"\.(?:hermes|openclaw)\b")
_DEBRAND_LEGAL_LINE = re.compile(r"(?:Copyright|©|\(c\)\s*\d{4})[^\n]*")
#: The frontmatter ``hermes:`` schema key (agentskills.io) is a wire identifier.
_DEBRAND_YAML_KEY = re.compile(r"(?m)^(\s*)hermes:(\s*)$")
_DEBRAND_WORD = re.compile(
    r"\b(Hermes|HERMES|hermes|Nous|NOUS|nous|OpenClaw|OPENCLAW)\b")
#: Same, without ``OpenClaw``: for skills that document the OpenClaw product
#: itself (migration source / gateway operations), where the name is load-bearing.
_DEBRAND_WORD_NO_OPENCLAW = re.compile(
    r"\b(Hermes|HERMES|hermes|Nous|NOUS|nous)\b")
#: Upstream docs site (``hermes-agent.nousresearch.com``). Skill references that
#: point readers at the upstream documentation are a product-surface leak, so the
#: RENDERED host is rewritten to the Qubit-owned docs endpoint. Applied *before*
#: :data:`_DEBRAND_PROTECT_SPAN`, which would otherwise mask the whole URL and
#: leave the brand in place. Provenance/install URLs (github.com/NousResearch,
#: portal.nousresearch.com, install.sh) are untouched by design: they name the
#: real upstream source and are preserved, not rendered.
_DEBRAND_DOCS_HOST = re.compile(
    r"https?://hermes-agent\.nousresearch\.com(/[\w./#?=&%+-]*)?")
_DEBRAND_DOCS_ENDPOINT = "/agent/openapi.json"
#: Doc-only home rewrites (user-facing paths -> the canonical QUBIT_HOME).
_DEBRAND_HOME_DOC = re.compile(r"(?:~|\$\{HOME\}|\$HOME)/\.hermes")
_DEBRAND_QUBIT_HOME_DOC = re.compile(r"\bHERMES_HOME\b")
#: Collapse a QUBIT_HOME parameter expansion whose fallback is the legacy home
#: (``${QUBIT_HOME:-~/.hermes}`` / ``${HERMES_HOME:-$HOME/.hermes}``) to the
#: canonical ``$QUBIT_HOME`` instead of leaving a degenerate ``${X:-$QUBIT_HOME}``.
_DEBRAND_HOME_FALLBACK_DOC = re.compile(
    r"\$\{(?:QUBIT_HOME|HERMES_HOME):-[^}]*?(?:~|\$\{HOME\}|\$HOME)/\.hermes\}")
_DEBRAND_HOME_SELF_DOC = re.compile(
    r"\$\{(?:QUBIT_HOME|HERMES_HOME):-\$(?:QUBIT_HOME|HERMES_HOME)\}")
#: Old skill names as skill references (not path segments / longer hyphenated
#: ids), so ``NousResearch/hermes-agent`` provenance and ``hermes-agent-dev``
#: stay verbatim. Longest first so the compound name wins.
_DEBRAND_SKILL_RENAMES = tuple(
    sorted(LEGACY_SKILL_NAME_RENAMES.items(), key=lambda kv: -len(kv[0])))


def _debrand_case_like(src: str, dst: str) -> str:
    if src.isupper():
        return dst.upper()
    if src[:1].isupper():
        return dst.capitalize()
    return dst


def debrand_skill_text(
    text: str, *, is_doc: bool = False, rewrite_openclaw: bool = True,
) -> str:
    """De-brand upstream assistant names in a skill text file.

    ``Hermes``/``Nous``/``OpenClaw`` prose -> ``Qubit`` (``Nous Research`` ->
    ``Qubit Labs``); in docs, ``~/.hermes`` -> ``$QUBIT_HOME`` and
    ``HERMES_HOME`` -> ``QUBIT_HOME``, and the upstream docs host
    (``hermes-agent.nousresearch.com``) -> the Qubit-owned
    ``/agent/openapi.json``. Provenance/install URLs (github.com/NousResearch,
    portal.nousresearch.com, install.sh), e-mail/handles, dotted wire ids,
    hyphenated service/plugin ids, quoted bare ids, external ``.hermes``/
    ``.openclaw`` paths, the ``metadata.hermes`` schema key, legal attribution
    lines and the explicit preserve list are all left verbatim. Idempotent.

    ``rewrite_openclaw=False`` keeps the OpenClaw product name for skills where
    it is load-bearing (a migration source or a gateway being operated).
    """
    masks: List[str] = []

    def _mask(m: re.Match) -> str:
        masks.append(m.group(0))
        return f"\x00{len(masks) - 1}\x00"

    out = text
    if is_doc:
        out = _DEBRAND_HOME_FALLBACK_DOC.sub("$QUBIT_HOME", out)
        out = _DEBRAND_HOME_SELF_DOC.sub("$QUBIT_HOME", out)
        out = _DEBRAND_QUBIT_HOME_DOC.sub("QUBIT_HOME", out)
        out = _DEBRAND_HOME_DOC.sub("$QUBIT_HOME", out)
    # Collapse the upstream docs host to the Qubit-owned endpoint before the
    # general URL mask hides it (see _DEBRAND_DOCS_HOST).
    out = _DEBRAND_DOCS_HOST.sub(_DEBRAND_DOCS_ENDPOINT, out)
    out = _DEBRAND_PROTECT_SPAN.sub(_mask, out)
    out = _DEBRAND_WIRE_DOTTED.sub(_mask, out)
    for old, new in _DEBRAND_SKILL_RENAMES:
        out = re.sub(rf"(?<![/\w.-]){re.escape(old)}(?![\w.-])", new, out)
    out = _DEBRAND_WIRE_HYPHEN.sub(_mask, out)
    out = _DEBRAND_QUOTED_ID.sub(_mask, out)
    out = _DEBRAND_DOT_HOME.sub(_mask, out)
    out = _DEBRAND_LEGAL_LINE.sub(_mask, out)
    out = _DEBRAND_YAML_KEY.sub(_mask, out)
    for s in _DEBRAND_PRESERVE_STRINGS:
        if s in out:
            out = out.replace(s, _mask(re.match(re.escape(s), s)))
    for src, dst in _DEBRAND_PHRASES:
        if src in out:
            out = out.replace(src, dst)
    word_re = _DEBRAND_WORD if rewrite_openclaw else _DEBRAND_WORD_NO_OPENCLAW
    out = word_re.sub(
        lambda m: _debrand_case_like(
            m.group(1), _DEBRAND_PROSE_WORDS[m.group(1).lower()]),
        out)

    def _restore(m: re.Match) -> str:
        return masks[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, out)


# --- rename/migration over a skills tree ------------------------------------
_DEBRAND_TEXT_SUFFIXES = frozenset((
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json",
    ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sh", ".bash", ".service",
    ".timer", ".md", ".mdx", ".html", ".css", ".nix", ".ps1", ".txt", ".rst",
    ".tmpl", ".tpl", ".template", ".j2",
))
_DEBRAND_DOC_SUFFIXES = frozenset((".md", ".mdx", ".txt", ".rst"))


def iter_skill_text_files(root: Path):
    """Yield text files under *root*; skip VCS/cache/backup dirs and dot-state files."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_SKILL_DIRS]
        for f in files:
            if f.startswith("."):
                continue
            p = Path(dirpath) / f
            if p.suffix.lower() in _DEBRAND_TEXT_SUFFIXES:
                yield p


def _iter_skill_dirs(base: Path):
    for skill_md in iter_skill_index_files(base, "SKILL.md"):
        yield skill_md.parent


def _migrate_frontmatter_name(skill_md: Path, old: str, new: str) -> bool:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return False
    new_text = re.sub(
        rf"(?m)^(name:\s*){re.escape(old)}(\s*)$", rf"\g<1>{new}\g<2>",
        text, count=1)
    if new_text == text:
        return False
    skill_md.write_text(new_text, encoding="utf-8")
    return True


def _migrate_keyed_lines(path: Path, old: str, new: str) -> bool:
    """Rename a ``name[:rest]`` key in a line-keyed state file (manifest/suppressed)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    out: List[str] = []
    changed = False
    seen_new = any(line.split(":", 1)[0].strip() == new for line in lines)
    for line in lines:
        if line.split(":", 1)[0].strip() == old:
            changed = True
            if seen_new:
                continue  # canonical new key already present; drop the legacy line
            rest = line.split(":", 1)[1] if ":" in line else ""
            out.append(f"{new}:{rest}" if rest else new)
            seen_new = True
        else:
            out.append(line)
    if changed:
        path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return changed


def _migrate_usage_keys(path: Path, old: str, new: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or old not in data:
        return False
    if new not in data:
        data[new] = data.pop(old)
    else:
        data.pop(old)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8")
    return True


def migrate_legacy_skill_dirs(
    skills_dir: Optional[Path] = None, *, apply: bool = False,
    backup_dir: Optional[Path] = None, retire: bool = False,
) -> Dict[str, Any]:
    """Non-destructive read-old/write-new rename of legacy-named skill dirs.

    Moves each :data:`LEGACY_SKILL_NAME_RENAMES` directory to its Qubit name
    (never deleting: an existing target is left untouched and reported),
    rewrites the ``SKILL.md`` frontmatter ``name:``, and migrates the
    ``.usage.json`` / ``.bundled_manifest`` / ``.curator_suppressed`` keys.
    ``apply=False`` is a dry-run. With *backup_dir*, each moved dir is copied
    there first. Returns a JSON-able report.

    ``retire=True`` additionally handles the half-migrated case an interrupted
    rename leaves behind: the Qubit target already exists *and* a leftover
    legacy-named frame still sits beside it. The frame is moved to
    ``<base>/.archive/<old>`` (the skills tree's ignored archive convention)
    after being copied to *backup_dir* — never merged and never deleted, so no
    content is lost and a stale duplicate cannot shadow the canonical skill.
    """
    base = Path(skills_dir) if skills_dir else get_skills_dir()
    report: Dict[str, Any] = {
        "skills_dir": str(base), "renamed": [], "skipped": [], "retired": [],
        "usage_keys": [], "manifest_keys": [], "suppressed_keys": [],
        "backups": [],
    }
    if not base.is_dir():
        return report
    archive = base / ".archive"
    for old, new in _DEBRAND_SKILL_RENAMES:
        old_dir = next((d for d in _iter_skill_dirs(base) if d.name == old
                        and archive not in d.parents), None)
        if old_dir is None:
            continue
        new_dir = old_dir.with_name(new)
        if new_dir.exists():
            if not retire:
                report["skipped"].append(
                    {"old": str(old_dir), "reason": f"{new} already exists"})
                continue
            report["retired"].append({"old": str(old_dir), "new": str(new_dir)})
            if not apply:
                continue
            if backup_dir is not None:
                dest = Path(backup_dir) / old
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(old_dir, dest)
                report["backups"].append(str(dest))
            covered = new_dir / "SKILL.md"
            frame = old_dir / "SKILL.md"
            if covered.is_file() and frame.is_file():
                # The canonical skill carries the content; the leftover frame is
                # a stale duplicate. Archive it (never delete).
                archive.mkdir(parents=True, exist_ok=True)
                target = archive / old
                if target.exists():
                    shutil.rmtree(target)
                shutil.move(str(old_dir), str(target))
            else:
                # No canonical content exists at the new name: complete the
                # rename so the skill is not lost.
                old_dir.rename(new_dir)
                if (new_dir / "SKILL.md").is_file():
                    _migrate_frontmatter_name(new_dir / "SKILL.md", old, new)
            continue
        report["renamed"].append({"old": str(old_dir), "new": str(new_dir)})
        if not apply:
            continue
        if backup_dir is not None:
            dest = Path(backup_dir) / old
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copytree(old_dir, dest)
            report["backups"].append(str(dest))
        old_dir.rename(new_dir)
        skill_md = new_dir / "SKILL.md"
        if skill_md.is_file():
            _migrate_frontmatter_name(skill_md, old, new)
    if apply:
        for state, key, migrator in (
            (base / ".usage.json", "usage_keys", _migrate_usage_keys),
            (base / ".bundled_manifest", "manifest_keys", _migrate_keyed_lines),
            (base / ".curator_suppressed", "suppressed_keys", _migrate_keyed_lines),
        ):
            if not state.is_file():
                continue
            for old, new in LEGACY_SKILL_NAME_RENAMES.items():
                if migrator(state, old, new):
                    report[key].append({"old": old, "new": new})
    return report


def debrand_skill_tree(
    root: Path, *, apply: bool = False, backup_dir: Optional[Path] = None,
    rewrite_openclaw: bool = True,
) -> Dict[str, Any]:
    """De-brand every text file under *root* (see :func:`debrand_skill_text`).

    Dry-run by default; ``apply=True`` writes. With *backup_dir*, each changed
    file is copied there (preserving its relative path) before it is written.
    """
    root = Path(root)
    report: Dict[str, Any] = {
        "root": str(root), "scanned": 0, "changed": [], "backups": []}
    for p in iter_skill_text_files(root):
        report["scanned"] += 1
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = debrand_skill_text(
            text, is_doc=p.suffix.lower() in _DEBRAND_DOC_SUFFIXES,
            rewrite_openclaw=rewrite_openclaw)
        if new == text:
            continue
        rel = p.relative_to(root)
        report["changed"].append(str(rel))
        if not apply:
            continue
        if backup_dir is not None:
            dest = Path(backup_dir) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(p, dest)
            report["backups"].append(str(dest))
        p.write_text(new, encoding="utf-8")
    return report


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.

def get_scan_ordered_skills_dirs() -> List[Path]:
    """All skill dirs in precedence order: project → local → external.

    First-wins name deduplication over this order gives project skills
    priority over profile-local and external ones.
    """
    dirs = list(get_project_skills_dirs())
    dirs.extend(get_all_skills_dirs())
    return dirs
# ---- END PLUGIN-COMPAT ----
