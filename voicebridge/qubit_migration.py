"""Safe, idempotent migration of the operator's real Qubit home into Qubit.

Design contract:

* **read-only source**: the operator's ``~/.hermes`` is opened
  ``mode=ro`` and is never modified or deleted;
* **Qubit-owned destination**: sessions/messages are imported through the
  canonical chat authority
  (:class:`voicebridge.qubit_session_store.QubitSessionStore` over the
  vendored ``qubit_state.SessionDB``, ``state.db``), preserving session ids,
  timestamps, message ordering, and tool/system rows — so migrated history is
  visible in BOTH ``/chat`` and ``/qubit-chat`` and every native surface;
* **backed up + reversible**: every apply copies the source databases and the
  destination store to a timestamped backup directory and writes a journal;
  :func:`rollback` restores the destination from that backup;
* **idempotent / restart-safe**: an existing session id is skipped, so a
  re-run imports only new sessions;
* **secret-safe**: only ``env:NAME`` *references* derived from variable names
  are recorded; secret values are never read, logged, or copied.

All roles are imported — user, assistant, system, and tool — together with
their tool_name/tool_call_id/tool_calls metadata, so nothing is silently
discarded (the canonical store preserves them). Rows that cannot be represented
(unknown role, or an empty non-tool row) are counted in ``skipped_roles`` and
recorded in the journal.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logging_setup import log_event

ENV_HOME = "QUBIT_HERMES_IMPORT_HOME"
JOURNAL_SCHEMA = "qubit-runtime-migration/1"
_SOURCE_DBS = ("state.db", "projects.db", "shared-state.db", "kanban.db",
               "sessions/state.db")
_VISIBLE_ROLES = ("user", "assistant", "system", "tool")

# Safe, non-secret user-state stores ported read-old/write-new (skip-existing).
# These never carry credential material. The CLI history (``.hermes_history``),
# request dumps, pairing, and gateway runtime state are deliberately excluded:
# the history holds pasted ``sk-`` credentials, the dumps hold raw prompts, and
# pairing/gateway files are Qubit-owned live state.
_SAFE_STATE_DIRS = ("memories", "checkpoints")
_SAFE_STATE_FILES = ("channel_directory.json",)
# Every remaining item is inventoried read-only for the coverage report so the
# import decision (and the omission) is explicit and reviewable.
_STATE_ITEM_DIRS = ("memories", "checkpoints", "pairing")
_STATE_ITEM_FILES = ("channel_directory.json", "gateway_state.json",
                     ".hermes_history")


# ---------------------------------------------------------------------------
# source resolution
# ---------------------------------------------------------------------------
def source_home(cfg: Any = None) -> Path:
    """Resolve the operator's Qubit home (never the Qubit-owned runtime home)."""
    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return Path(override).expanduser()
    if cfg is not None:
        try:
            configured = cfg.get("migration", "hermes_home", default=None)
        except Exception:
            configured = None
        if configured:
            return Path(str(configured)).expanduser()
    return Path.home() / ".hermes"


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")}


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------------------
# read-only inventory
# ---------------------------------------------------------------------------
def _state_item_report(home: Path) -> Dict[str, Any]:
    """Read-only present/count inventory of the remaining user-state items.

    Counts only — file contents and secret values are never read or returned.
    ``_SAFE_STATE_DIRS``/``_SAFE_STATE_FILES`` are the subset Qubit imports; the
    rest are reported so the omission is explicit and reviewable.
    """
    def _files(root: Path) -> int:
        if not root.is_dir():
            return 0
        return sum(1 for p in root.rglob("*")
                   if p.is_file() and not p.name.endswith(".lock"))

    def _lines(p: Path) -> int:
        if not p.is_file():
            return 0
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    items: Dict[str, Any] = {}
    for name in _STATE_ITEM_DIRS:
        d = home / name
        items[name] = {"present": d.is_dir(), "files": _files(d)}
    for name in _STATE_ITEM_FILES:
        f = home / name
        entry: Dict[str, Any] = {
            "present": f.is_file(),
            "bytes": f.stat().st_size if f.is_file() else 0,
        }
        if name == ".hermes_history":
            entry["lines"] = _lines(f)
        items[name] = entry
    # kanban/projects row counts live in their SQLite stores; report them here
    # so the coverage table is self-contained.
    for rel, table in (("kanban.db", "tasks"), ("projects.db", "projects")):
        p = home / rel
        entry = {"present": p.is_file(), "rows": 0}
        if p.is_file():
            try:
                conn = _connect_ro(p)
                if table in _tables(conn):
                    entry["rows"] = _scalar(conn,
                                            f"select count(*) from {table}")
                conn.close()
            except sqlite3.Error:
                entry["error"] = "unreadable"
        items[rel] = entry
    return items


def inspect(cfg: Any = None, *, home: Optional[Path] = None) -> Dict[str, Any]:
    """Read-only inventory of the source home (no contents, no secret values)."""
    home = Path(home) if home else source_home(cfg)
    out: Dict[str, Any] = {
        "state": "OK" if home.is_dir() else "MISSING",
        "home": str(home),
        "databases": {},
        "present_dbs": [],
        "config": {},
        "secret_refs": [],
        "dirs": {},
    }
    if not home.is_dir():
        return out
    for rel in _SOURCE_DBS:
        p = home / rel
        if not p.is_file():
            continue
        out["present_dbs"].append(rel)
        entry: Dict[str, Any] = {"counts": {}}
        try:
            conn = _connect_ro(p)
            tabs = _tables(conn)
            entry["tables"] = len(tabs)
            if "sessions" in tabs:
                entry["counts"]["sessions"] = _scalar(conn, "select count(*) from sessions")
                entry["counts"]["archived"] = _scalar(
                    conn, "select count(*) from sessions where archived=1")
            if "messages" in tabs:
                entry["counts"]["messages"] = _scalar(conn, "select count(*) from messages")
            if "projects" in tabs:
                entry["counts"]["projects"] = _scalar(conn, "select count(*) from projects")
            conn.close()
        except sqlite3.Error as exc:
            entry["error"] = type(exc).__name__
        out["databases"][rel] = entry
    cfg_yaml = home / "config.yaml"
    if cfg_yaml.is_file():
        try:
            import yaml

            data = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                model = data.get("model")
                provider = model if isinstance(model, str) else (
                    model.get("provider") if isinstance(model, dict) else None)
                default = model.get("default") if isinstance(model, dict) else (
                    model if isinstance(model, str) else None)
                out["config"] = {
                    "top_level_keys": sorted(str(k) for k in data.keys())[:60],
                    "model_provider": provider,
                    "model_default": default,
                }
        except Exception:
            out["config"] = {"error": "unreadable"}
    envf = home / ".env"
    if envf.is_file():
        names: List[str] = []
        try:
            for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name = line.split("=", 1)[0].strip()
                if name:
                    names.append(name)
        except OSError:
            pass
        # References only: never the values.
        out["secret_refs"] = [f"env:{n}" for n in sorted(set(names))]
    for sub in ("skills", "plugins", "hooks", "cron", "attachments"):
        d = home / sub
        if d.is_dir():
            out["dirs"][sub] = sum(1 for x in d.rglob("*") if not x.is_dir())
    out["mcp_servers"] = sorted(_source_mcp_servers(home).keys())
    out["state_items"] = _state_item_report(home)
    return out


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------
def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    return str(value)


# Message columns carried through migration when present in the source schema
# (a superset across Qubit schema generations).
_MESSAGE_COLUMNS = (
    "id", "role", "content", "tool_name", "tool_call_id", "tool_calls",
    "reasoning", "reasoning_content", "reasoning_details",
    "codex_reasoning_items", "codex_message_items", "token_count",
    "finish_reason", "effect_disposition", "platform_message_id", "observed",
    "api_content", "display_kind", "display_metadata", "timestamp",
)


def _source_system_prompts(conn: sqlite3.Connection,
                           hashes: set) -> Dict[str, str]:
    """The referenced ``system_prompts`` rows so a mirrored hash stays valid."""
    if not hashes or "system_prompts" not in _tables(conn):
        return {}
    out: Dict[str, str] = {}
    try:
        q = ("select hash, prompt from system_prompts where hash in ("
             + ",".join("?" for _ in hashes) + ")")
        for row in conn.execute(q, tuple(sorted(hashes))):
            if row[0] is not None:
                out[str(row[0])] = str(row[1] or "")
    except sqlite3.Error:
        return {}
    return out


def _source_sessions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    tabs = _tables(conn)
    if "sessions" not in tabs:
        return []
    # Insertion order (rowid), never timestamp ordering: Qubit orders
    # transcripts by insertion id, and timestamp ties would scramble sessions.
    rows = conn.execute("select * from sessions order by rowid asc")
    names = [d[0] for d in rows.description]
    out: List[Dict[str, Any]] = []
    for row in rows:
        rec = dict(zip(names, row))
        sid = str(rec.get("id") or "").strip()
        if not sid:
            continue
        # Keep EVERY source column; add display aliases for the importer.
        rec["id"] = sid
        title = (rec.get("title") or rec.get("display_name") or rec.get("name")
                 or "")
        rec["name"] = str(title or "").strip()
        if not rec.get("source"):
            rec["source"] = "hermes"
        if not rec.get("started_at") and rec.get("created_at"):
            rec["started_at"] = rec.get("created_at")
        if not rec.get("last_activity_at") and rec.get("updated_at"):
            rec["last_activity_at"] = rec.get("updated_at")
        out.append(rec)
    return out


def _source_messages(conn: sqlite3.Connection, sid: str
                     ) -> tuple[List[Dict[str, Any]], int]:
    tabs = _tables(conn)
    if "messages" not in tabs:
        return [], 0
    cols = {r[1] for r in conn.execute("pragma table_info(messages)")}
    wanted = [c for c in _MESSAGE_COLUMNS if c in cols]
    if "role" not in wanted or "content" not in wanted:
        return [], 0
    # Insertion order: Qubit' own authority orders transcript rows by id and
    # explicitly warns against timestamp ordering (ties/shared clocks).
    order = "id" if "id" in cols else "rowid"
    rows = conn.execute(
        f"select {', '.join(wanted)} from messages "
        f"where session_id = ? order by {order} asc, rowid asc", (sid,))
    names = [d[0] for d in rows.description]
    out: List[Dict[str, Any]] = []
    skipped = 0
    for row in rows:
        rec = dict(zip(names, row))
        role = str(rec.get("role") or "")
        if role not in _VISIBLE_ROLES:
            skipped += 1
            continue
        text = _coerce_text(rec.get("content"))
        tool_calls = rec.get("tool_calls")
        reasoning = rec.get("reasoning") or rec.get("reasoning_content")
        # A reasoning-only assistant row is real history; never drop it.
        if not text.strip() and not tool_calls and not reasoning:
            skipped += 1
            continue
        ts = rec.get("timestamp")
        entry: Dict[str, Any] = {
            "role": role, "content": text,
            "timestamp": float(ts) if isinstance(ts, (int, float)) else None}
        for c in _MESSAGE_COLUMNS:
            if c in ("id", "role", "content", "timestamp"):
                continue
            if rec.get(c) is not None:
                entry[c] = rec[c]
        # Preserve source message identity: the row id AND the platform id are
        # both carried (import_session forwards them to the vendored columns).
        if rec.get("id") is not None:
            entry["message_id"] = rec["id"]
        out.append(entry)
    return out, skipped


# ---------------------------------------------------------------------------
# plan / apply / rollback
# ---------------------------------------------------------------------------
def _dest_store(cfg: Any):
    from .chat_transport import store as chat_store

    return chat_store(cfg)


# ---------------------------------------------------------------------------
# asset import: profiles/model-provider, skills, plugins, hooks, schedules
# ---------------------------------------------------------------------------
def _qubit_home(cfg: Any = None) -> Path:
    from . import qubit_runtime

    qubit_runtime.ensure_path()
    return qubit_runtime.runtime_home_for_cfg(cfg)


def _read_env_map(home: Path) -> Dict[str, str]:
    """Read ``.env`` into memory for reference matching (values never emitted)."""
    out: Dict[str, str] = {}
    envf = Path(home) / ".env"
    if not envf.is_file():
        return out
    try:
        for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _import_model_profile(cfg: Any, home: Path) -> Dict[str, Any]:
    """Mirror the source ``model`` section as a Qubit API profile (env ref only)."""
    cfg_yaml = Path(home) / "config.yaml"
    if not cfg_yaml.is_file():
        return {"state": "MISSING", "detail": "no source config.yaml"}
    try:
        import yaml

        data = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return {"state": "FAILED",
                "detail": f"unreadable config.yaml: {type(exc).__name__}"}
    model = data.get("model")
    if isinstance(model, str):
        model = {"default": model}
    if not isinstance(model, dict):
        return {"state": "MISSING", "detail": "no model section"}
    provider = str(model.get("provider") or "").strip()
    model_id = str(model.get("default") or model.get("model")
                   or model.get("name") or "").strip()
    base_url = str(model.get("base_url") or "").strip()
    if not provider or not model_id:
        return {"state": "MISSING", "detail": "model section lacks provider/model"}
    key_ref = ""
    api_key = model.get("api_key")
    if api_key:
        for name, value in _read_env_map(home).items():
            if value and value == api_key:
                key_ref = f"env:{name}"
                break
    from .providers import resolver as R

    env_map = _read_env_map(home)

    def _key_ref(section: Dict[str, Any]) -> str:
        api_key = section.get("api_key")
        if not api_key:
            return ""
        for name, value in env_map.items():
            if value and value == api_key:
                return f"env:{name}"
        return ""

    def _mk(suffix: str, section: Dict[str, Any]) -> Optional[Any]:
        prov = str(section.get("provider") or "").strip()
        mid = str(section.get("default") or section.get("model")
                  or section.get("name") or "").strip()
        if not prov or not mid:
            return None
        try:
            ctx = int(section.get("context") or section.get("max_context") or 0)
        except (TypeError, ValueError):
            ctx = 0
        pid = ("qubit-" + suffix).replace(":", "-")[:48]
        return R.ModelProfile(
            id=pid, kind=R.API, provider=prov, model=mid,
            base_url=str(section.get("base_url") or "").strip(),
            key_ref=_key_ref(section), locality="external",
            egress_consent="allow", roles=[R.DEFAULT_ROLE],
            context_tokens=ctx,
            provenance=f"migrated from {home}/config.yaml {suffix}")

    # B-04: import the model section plus fallback_providers and auxiliary
    # (side-LLM) provider/model sections, not just `model`.
    candidates: List[tuple] = [("model", model)]
    fallbacks = data.get("fallback_providers")
    if isinstance(fallbacks, list):
        for i, fb in enumerate(fallbacks):
            if isinstance(fb, dict):
                candidates.append((f"fallback-{i + 1}", fb))
    auxiliary = data.get("auxiliary")
    if isinstance(auxiliary, dict):
        for task, sec in sorted(auxiliary.items()):
            if isinstance(sec, dict):
                candidates.append((f"aux-{task}", sec))
    # B-04M: speech/voice profile sections are real provider selections too.
    for key in ("stt", "tts", "voice", "speech", "realtime"):
        sec = data.get(key)
        if isinstance(sec, dict):
            candidates.append((key, sec))

    profiles: List[Dict[str, Any]] = []
    errors: List[str] = []
    for suffix, section in candidates:
        profile = _mk(suffix, section)
        if profile is None:
            continue
        # Idempotency: skip the write when the destination already holds an
        # identical record. ``upsert_model_profile`` rewrites the registry file
        # even when the content is unchanged, which would bump the mtime on
        # every re-run; a true no-op keeps the destination byte- and
        # mtime-stable.
        existing = R.get_profile(cfg, profile.id)
        if existing is not None \
                and existing.to_record() == profile.to_record():
            profiles.append({"profile_id": profile.id,
                             "key_ref": profile.key_ref,
                             "context_tokens": profile.context_tokens,
                             "state": "EXISTS"})
            continue
        try:
            res = R.upsert_model_profile(cfg, profile, confirm=True)
            profiles.append({"profile_id": profile.id,
                             "key_ref": profile.key_ref,
                             "context_tokens": profile.context_tokens,
                             "state": res.get("state", "UNKNOWN")})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{profile.id}: {type(exc).__name__}")
    if not profiles:
        return {"state": "FAILED", "profile_id": "", "key_ref": key_ref,
                "detail": "no importable model/provider section",
                "errors": errors}
    first = profiles[0]
    return {"state": "OK" if not errors else "PARTIAL",
            "profile_id": first["profile_id"], "key_ref": first["key_ref"],
            "context_tokens": first["context_tokens"],
            "profiles": profiles, "errors": errors,
            "auxiliary_sections": sorted(auxiliary.keys())
            if isinstance(auxiliary, dict) else [],
            "detail": str(len(profiles)) + " profile(s) imported"}


def _copy_missing(src_dir: Path, dst_dir: Path) -> List[str]:
    """Copy files absent from the destination (idempotent, never overwrites)."""
    added: List[str] = []
    if not src_dir.is_dir():
        return added
    for p in sorted(src_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src_dir)
        if any(part in ("__pycache__", ".git") for part in rel.parts):
            continue
        if p.name.endswith((".lock", ".tmp", ".pyc")):
            continue
        target = dst_dir / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        added.append(str(target))
    return added


def _merge_cron(src_home: Path, dst_home: Path) -> Dict[str, Any]:
    """Merge source ``cron/jobs.json`` jobs into the destination by job id."""
    src = src_home / "cron" / "jobs.json"
    if not src.is_file():
        return {"added": 0, "ids": []}
    try:
        sdata = json.loads(src.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"added": 0, "ids": [], "error": "unreadable jobs.json"}
    jobs = sdata.get("jobs") if isinstance(sdata, dict) else sdata
    if not isinstance(jobs, list):
        return {"added": 0, "ids": []}
    dstf = dst_home / "cron" / "jobs.json"
    dst_data: Dict[str, Any] = {"jobs": [], "updated_at": time.time()}
    if dstf.is_file():
        try:
            loaded = json.loads(dstf.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("jobs"), list):
                dst_data = loaded
        except Exception:  # noqa: BLE001
            pass
    have = {str(j.get("id")) for j in dst_data["jobs"] if isinstance(j, dict)}
    added_ids: List[str] = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        jid = str(j.get("id") or "")
        if not jid or jid in have:
            continue
        dst_data["jobs"].append(j)
        have.add(jid)
        added_ids.append(jid)
    if added_ids:
        dstf.parent.mkdir(parents=True, exist_ok=True)
        tmp = dstf.with_name(dstf.name + ".qubit-tmp")
        tmp.write_text(json.dumps(dst_data, indent=2), encoding="utf-8")
        os.replace(tmp, dstf)
    return {"added": len(added_ids), "ids": added_ids}


# ---------------------------------------------------------------------------
# skill/plugin enabled-state import (Objective B remainder)
# ---------------------------------------------------------------------------
def _normalize_skill_names(value: Any) -> Optional[set]:
    """Config value -> set of non-empty skill names, or ``None`` if unknown.

    Mirrors the vendored ``agent.skill_utils`` normalization: ``None`` (YAML
    null) is empty, a bare scalar is a single name (not its characters), and a
    JSON/Python-literal list string (``qubit config set`` stores lists that way)
    is parsed. ``None`` is returned for a type that cannot be represented as
    names so a caller refuses to overwrite an unrecognized destination value
    instead of mangling it.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            if isinstance(parsed, list):
                return {str(item).strip() for item in parsed
                        if str(item).strip()}
        return {text} if text else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value if str(item).strip()}
    return None


def _platform_disabled_names(value: Any) -> set:
    """Union of ``skills.platform_disabled`` names (per-platform dict or list)."""
    names: set = set()
    if isinstance(value, dict):
        for entry in value.values():
            names |= _normalize_skill_names(entry) or set()
    else:
        names |= _normalize_skill_names(value) or set()
    return names


def _source_skill_state(home: Path, yaml: Any) -> Dict[str, Any]:
    """Read-only source ``skills.disabled`` / ``skills.platform_disabled``."""
    out: Dict[str, Any] = {"state": "MISSING", "disabled": set(),
                           "platform_disabled": set()}
    src = home / "config.yaml"
    if not src.is_file():
        return out
    try:
        data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - a bad source never aborts import
        out["state"] = "UNREADABLE"
        out["error"] = type(exc).__name__
        return out
    if not isinstance(data, dict):
        out["state"] = "UNREADABLE"
        return out
    skills = data.get("skills")
    if isinstance(skills, dict):
        out["disabled"] = _normalize_skill_names(skills.get("disabled")) or set()
        out["platform_disabled"] = _platform_disabled_names(
            skills.get("platform_disabled"))
    out["state"] = "OK"
    return out


def _import_skill_state(cfg: Any, home: Path) -> Dict[str, Any]:
    """Union the source skill-disabled state into the Qubit runtime config.

    Read-only on the source and strictly additive on the destination: the
    source ``skills.disabled`` and every ``skills.platform_disabled`` name are
    merged into ``<runtime_home>/config.yaml`` ``skills.disabled`` as a UNION.
    An existing disabled entry is never removed, an existing destination key is
    never overwritten, and the rest of the destination config is preserved. The
    write is atomic; an unreadable or unrecognized destination config is left
    untouched and reported as a typed skip rather than risk data loss. A re-run
    adds nothing (idempotent).
    """
    report: Dict[str, Any] = {
        "state": "SKIPPED", "source_disabled": [], "added": [],
        "already_present": [], "platform_added": [], "skipped": [],
        "code": "", "detail": "",
    }
    try:
        import yaml
    except Exception:  # noqa: BLE001 - PyYAML is a hard dependency of the fork
        report.update(state="SKIPPED", code="SKILL_STATE_NO_YAML",
                      detail="PyYAML unavailable; destination config not "
                             "touched")
        report["skipped"].append("PyYAML unavailable")
        return report
    source = _source_skill_state(home, yaml)
    if source["state"] == "MISSING":
        report.update(state="MISSING", code="SKILL_STATE_NO_SOURCE",
                      detail="no source config.yaml")
        return report
    if source["state"] == "UNREADABLE":
        report.update(state="SKIPPED", code="SKILL_STATE_SOURCE_UNREADABLE",
                      detail="source config.yaml unreadable; destination "
                             "config not touched")
        report["skipped"].append("source config.yaml unreadable")
        return report
    source_all = set(source["disabled"]) | set(source["platform_disabled"])
    # ``source_disabled`` is every disabled name found in the source (global
    # plus platform); ``added``/``already_present`` partition it against the
    # destination and ``platform_added`` isolates the platform contribution.
    report["source_disabled"] = sorted(source_all)
    if not source_all:
        report.update(state="NOOP", code="",
                      detail="source has no disabled skills")
        return report

    dest_yaml = _qubit_home(cfg) / "config.yaml"
    dest_data: Dict[str, Any] = {}
    if dest_yaml.is_file():
        try:
            loaded = yaml.safe_load(dest_yaml.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - never corrupt a bad file
            report.update(
                state="SKIPPED", code="SKILL_STATE_DEST_UNREADABLE",
                detail=f"destination config.yaml unreadable "
                       f"({type(exc).__name__}); left untouched")
            report["skipped"].append("destination config.yaml unreadable")
            return report
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            report.update(
                state="SKIPPED", code="SKILL_STATE_DEST_NOT_MAPPING",
                detail="destination config.yaml is not a mapping; left "
                       "untouched")
            report["skipped"].append("destination config.yaml is not a mapping")
            return report
        dest_data = loaded
    skills = dest_data.get("skills")
    if skills is None:
        skills = {}
    elif not isinstance(skills, dict):
        report.update(
            state="SKIPPED", code="SKILL_STATE_DEST_SKILLS_NOT_MAPPING",
            detail="destination skills is not a mapping; left untouched")
        report["skipped"].append("destination skills is not a mapping")
        return report
    existing = _normalize_skill_names(skills.get("disabled"))
    if existing is None:
        report.update(
            state="SKIPPED", code="SKILL_STATE_DEST_DISABLED_UNKNOWN",
            detail="destination skills.disabled is an unrecognized type; left "
                   "untouched")
        report["skipped"].append("destination skills.disabled unrecognized")
        return report
    added = sorted(source_all - existing)
    report["already_present"] = sorted(source_all & existing)
    report["platform_added"] = sorted(
        set(source["platform_disabled"]) - existing)
    if not added:
        report.update(state="NOOP", code="",
                      detail="destination already contains every source "
                             "disabled skill")
        return report
    skills["disabled"] = sorted(existing | set(added))
    dest_data["skills"] = skills
    dest_yaml.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_yaml.with_name(dest_yaml.name + ".qubit-tmp")
    tmp.write_text(
        yaml.safe_dump(dest_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    os.replace(tmp, dest_yaml)
    report["added"] = added
    report["path"] = str(dest_yaml)
    report.update(state="OK", code="",
                  detail=f"{len(added)} disabled skill name(s) merged")
    return report


def _source_plugin_states(home: Path) -> List[Dict[str, Any]]:
    """Read-only ``plugins/<name>/state.json`` enabled/disabled hints.

    Source plugin dirs store arbitrary per-plugin state; only an explicit
    ``enabled``/``disabled`` boolean (or a scalar ``state``) is surfaced.
    Missing files and unrecognized payloads are reported ``UNKNOWN``, never
    guessed.
    """
    out: List[Dict[str, Any]] = []
    root = home / "plugins"
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        state = "UNKNOWN"
        state_file = entry / "state.json"
        if state_file.is_file():
            try:
                payload = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, dict):
                if isinstance(payload.get("enabled"), bool):
                    state = "ENABLED" if payload["enabled"] else "DISABLED"
                elif isinstance(payload.get("disabled"), bool):
                    state = "DISABLED" if payload["disabled"] else "ENABLED"
                elif isinstance(payload.get("state"), str):
                    state = payload["state"].strip().upper()[:24] or "UNKNOWN"
        out.append({"name": entry.name, "state": state})
    return out


def _import_plugin_state(cfg: Any, home: Path) -> Dict[str, Any]:
    """Report source plugin enable/disable state; there is no safe API.

    ``voicebridge.plugin_runtime`` validates a full manifest
    (``id``/``kind``/``entrypoint``/``digest``) through a confirmation-gated
    ``register``/``install`` and stores no enable/disable field; its load/save
    helpers are private. There is therefore no public API to record a plugin
    NAME + enabled/disabled state, and inventing a second store is forbidden.
    This reads the source state (read-only) and returns a typed ``unsupported``
    report naming the exact reason instead of fabricating a destination write.
    """
    return {
        "state": "UNSUPPORTED",
        "code": "PLUGIN_STATE_NO_PUBLIC_API",
        "detail": "voicebridge.plugin_runtime has no public API to record a "
                  "plugin enable/disable state: register()/install() require a "
                  "full validated manifest and a confirmation-gated write, the "
                  "store has no enabled/disabled field, and its _load/_save "
                  "helpers are private. No second store was created.",
        "source_plugins": _source_plugin_states(home),
        "recorded": [],
    }


def _source_mcp_servers(home: Path) -> Dict[str, Dict[str, Any]]:
    """Read-only MCP server definitions from the source home.

    Sources, in order (later entries win by name):
    * ``config.yaml`` ``mcp_servers:`` block (the vendored runtime's key);
    * a source ``mcp_servers.json`` registry if one exists.

    Secret VALUES are never returned: env/auth values are left as-is here and
    converted to references by :func:`_import_mcp_servers`.
    """
    out: Dict[str, Dict[str, Any]] = {}
    cfg_yaml = home / "config.yaml"
    if cfg_yaml.is_file():
        try:
            import yaml

            data = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            data = {}
        raw = data.get("mcp_servers") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for name, rec in raw.items():
                if isinstance(rec, dict):
                    out[str(name)] = dict(rec)
                elif isinstance(rec, str):
                    out[str(name)] = {"command": rec}
    src_json = home / "mcp_servers.json"
    if src_json.is_file():
        try:
            doc = json.loads(src_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        servers = doc.get("servers") if isinstance(doc, dict) else None
        if isinstance(servers, dict):
            for name, rec in servers.items():
                if isinstance(rec, dict):
                    out.setdefault(str(name), dict(rec))
    return out


def _env_ref_for(value: Any, env_map: Dict[str, str]) -> Optional[str]:
    """Map a source env value to a reference, or None (never a raw secret).

    Accepts an existing ``vault:``/``env:``/``file:`` reference, a ``${VAR}``
    or ``$VAR`` interpolation, or a value that exactly matches a source ``.env``
    value (mapped to ``env:NAME``). A raw value with no match is dropped so a
    secret is never copied.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith(("vault:", "env:", "file:")):
        return text
    m = re.match(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$", text) or \
        re.match(r"^\$([A-Za-z_][A-Za-z0-9_]*)$", text)
    if m:
        return f"env:{m.group(1)}"
    for name, val in env_map.items():
        if val and val == text:
            return f"env:{name}"
    return None


def _import_mcp_servers(cfg: Any, home: Path) -> Dict[str, Any]:
    """Import compatible MCP server definitions into the canonical registry.

    Idempotent and non-destructive: an existing Qubit-owned record is never
    overwritten (it is reported as a conflict); only secret REFERENCES are
    written. Returns a per-item journal fragment.
    """
    from . import mcp_servers as MS

    source = _source_mcp_servers(home)
    env_map = _read_env_map(home)
    imported: List[str] = []
    skipped: List[str] = []
    conflicts: List[Dict[str, str]] = []
    unsupported: List[Dict[str, str]] = []
    if not source:
        return {"source_count": 0, "imported": [], "skipped": [],
                "conflicts": [], "unsupported": []}

    existing: Dict[str, Any] = {}
    try:
        existing = {s.get("name"): s for s in MS.list_servers(cfg).get(
            "servers", [])}
    except Exception:  # noqa: BLE001 - registry read must not abort import
        existing = {}

    for name, rec in sorted(source.items()):
        if not isinstance(rec, dict):
            unsupported.append({"name": name, "reason": "not-a-mapping"})
            continue
        if name in existing:
            skipped.append(name)
            conflicts.append({
                "name": name,
                "reason": "destination record exists; never overwritten",
                "action": "re-import skipped"})
            continue
        command = str(rec.get("command") or "").strip()
        url = str(rec.get("url") or "").strip()
        fields: Dict[str, Any] = {
            "enabled": bool(rec.get("enabled", False)),
        }
        if command:
            fields["transport"] = "stdio"
            fields["command"] = command
            args = rec.get("args")
            if isinstance(args, (list, tuple)):
                fields["args"] = [str(a) for a in args]
        elif url:
            fields["transport"] = "http"
            fields["url"] = url
        else:
            unsupported.append({"name": name,
                                "reason": "neither command nor url"})
            continue
        env = rec.get("env")
        refs: Dict[str, str] = {}
        dropped: List[str] = []
        if isinstance(env, dict):
            for key, value in env.items():
                ref = _env_ref_for(value, env_map)
                if ref:
                    refs[str(key)] = ref
                else:
                    dropped.append(str(key))
        if refs:
            fields["env"] = refs
        try:
            MS.upsert(cfg, name, fields)
            # ``upsert`` records the definition; the enabled flag is a
            # separate canonical transition, so apply it explicitly.
            MS.set_enabled(cfg, name, bool(fields.get("enabled", False)))
        except MS.McpServerError as exc:
            unsupported.append({"name": name, "reason": exc.code})
            continue
        imported.append(name)
        if dropped:
            unsupported.append({
                "name": name,
                "reason": f"env values without a resolvable reference were "
                          f"dropped (never copied): {', '.join(sorted(dropped))}"})
    return {"source_count": len(source), "imported": imported,
            "skipped": skipped, "conflicts": conflicts,
            "unsupported": unsupported}


def catalog_report(cfg: Any, home: Path) -> Dict[str, Any]:
    """Source-vs-destination catalog counts for skills/plugins/MCP.

    Read-only: counts real on-disk catalog items on both sides so an operator
    can verify the imported catalog matches the compatible source count.
    """
    def _skill_count(root: Path) -> int:
        if not root.is_dir():
            return 0
        return sum(1 for p in root.rglob("SKILL.md")
                   if ".archive" not in p.parts
                   and ".curator_backups" not in p.parts)

    def _dir_count(root: Path) -> int:
        if not root.is_dir():
            return 0
        return sum(1 for p in root.iterdir()
                   if p.is_dir() and not p.name.startswith("."))

    dest_home = _qubit_home(cfg)
    try:
        from . import mcp_servers as MS

        dest_mcp = int(MS.list_servers(cfg).get("total", 0))
    except Exception:  # noqa: BLE001
        dest_mcp = 0
    return {
        "source_home": str(home),
        "destination_home": str(dest_home),
        "skills": {"source": _skill_count(home / "skills"),
                   "destination": _skill_count(dest_home / "skills")},
        "plugins": {"source": _dir_count(home / "plugins"),
                    "destination": _dir_count(dest_home / "plugins")},
        "mcp": {"source": len(_source_mcp_servers(home)),
                "destination": dest_mcp},
    }


def _import_assets(cfg: Any, home: Path, bdir: Path, *,
                   safe: bool = False) -> Dict[str, Any]:
    """Import profiles/model-provider, skills, plugins, hooks, schedules, MCP.

    Returns a journal fragment; destination files that can be modified are
    backed up under ``bdir/destination`` and mapped for rollback. Only
    ``env:`` secret references are created — never a plaintext key.

    ``safe=True`` is the owner-approved non-secret subset: the reference
    provider/model profile and the MCP registry are **not** imported (owner
    decision #9 — the reference provider and its now-dead MCP servers must not
    be re-introduced), while the safe user-state stores (``memories``,
    ``checkpoints``, ``channel_directory.json``) are ported read-old/write-new.
    """
    dest_home = _qubit_home(cfg)
    restore_map: Dict[str, str] = {}
    created_files: List[str] = []

    def _backup(original: Path) -> None:
        if original.is_file():
            name = f"asset__{abs(hash(str(original))) % 10**8}__{original.name}"
            shutil.copy2(original, bdir / "destination" / name)
            restore_map[name] = str(original)

    state_dir = Path(cfg.state_dir()) if hasattr(cfg, "state_dir") else None
    if state_dir is not None:
        _backup(state_dir / "profiles.json")
    _backup(dest_home / "cron" / "jobs.json")
    _backup(dest_home / "config.yaml")

    if safe:
        model_result: Dict[str, Any] = {
            "state": "SKIPPED", "code": "SAFE_MODE",
            "detail": "reference provider/model profile not imported "
                      "(owner decision #9: no reference provider config)"}
    else:
        model_result = _import_model_profile(cfg, home)
    added: List[str] = []
    # B-04: import projects, attachments, skills, plugins and hooks (not just
    # skills/plugins/hooks). Skip-existing, so no destination file is lost.
    for name in ("skills", "plugins", "hooks", "projects", "attachments"):
        added.extend(_copy_missing(home / name, dest_home / name))
    # W-MIG wave 17: port the remaining safe, non-secret user-state stores
    # read-old/write-new (skip-existing). ``memories``/``checkpoints`` are
    # directories; ``channel_directory.json`` is a single non-secret file. The
    # CLI history, request dumps, pairing, and gateway runtime state are never
    # imported (secrets / Qubit-owned live state).
    for name in _SAFE_STATE_DIRS:
        added.extend(_copy_missing(home / name, dest_home / name))
    for rel in _SAFE_STATE_FILES:
        src = home / rel
        target = dest_home / rel
        if src.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            added.append(str(target))
    # Objective B remainder: union the source skill-disabled state into the
    # destination runtime config.yaml (existing disabled entries preserved,
    # everything else untouched); rollback removes a config the import created.
    dest_cfg_existed = (dest_home / "config.yaml").is_file()
    skill_state = _import_skill_state(cfg, home)
    if not dest_cfg_existed and (dest_home / "config.yaml").is_file():
        created_files.append(str(dest_home / "config.yaml"))
    # Plugin enabled-state has no safe destination API (typed unsupported).
    plugin_state = _import_plugin_state(cfg, home)
    # Objective B: import MCP server definitions into the canonical registry.
    # The registry is backed up for rollback; a created store is removed on
    # rollback. Only reference-only env/auth values are written.
    if state_dir is not None:
        _backup(state_dir / "mcp_servers.json")
    mcp_existed = (state_dir is not None
                   and (state_dir / "mcp_servers.json").is_file())
    if safe:
        mcp_result: Dict[str, Any] = {
            "source_count": len(_source_mcp_servers(home)),
            "imported": [], "skipped": [], "conflicts": [], "unsupported": [],
            "state": "SKIPPED", "code": "SAFE_MODE",
            "detail": "MCP registry not imported (safe subset: avoids "
                      "re-introducing the reference's dead MCP servers)"}
    else:
        mcp_result = _import_mcp_servers(cfg, home)
    if (not safe and state_dir is not None and not mcp_existed
            and (state_dir / "mcp_servers.json").is_file()):
        created_files.append(str(state_dir / "mcp_servers.json"))
    # Receipts/audit: copy any real standalone store rather than fabricating a
    # disposition. Skip-existing keeps the Qubit-owned action store intact.
    receipt_added: List[str] = []
    for rel in ("receipts", "audit", "spawn-ledger.json", "receipts.json",
                "audit.jsonl"):
        src = home / rel
        if src.is_dir():
            receipt_added.extend(_copy_missing(src, dest_home / rel))
        elif src.is_file():
            target = dest_home / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                receipt_added.append(str(target))
    added.extend(receipt_added)
    # B-04M: auxiliary canonical databases the vendored runtime opens directly
    # (projects, hosted-room shared state, kanban) plus cron execution history.
    # Snapshot them WAL-safely into the destination home when absent; never
    # overwrite an existing Qubit-owned store. Rollback removes a created DB.
    aux_dbs: List[str] = []
    for rel in ("projects.db", "shared-state.db", "kanban.db",
                "cron/executions.db"):
        src = home / rel
        target = dest_home / rel
        if src.is_file() and not target.exists():
            try:
                _snapshot_sqlite(src, target)
            except Exception:
                continue
            created_files.append(str(target))
            aux_dbs.append(str(target))
    added.extend(_copy_missing(home / "cron" / "output",
                               dest_home / "cron" / "output"))
    # The credential store (auth.json) is never copied: it holds provider token
    # material and Qubit migrates references, not secrets. Recorded as a typed
    # disposition so the omission is explicit, with the reenrollment path.
    if (home / "auth.json").is_file():
        credentials_audit: Dict[str, Any] = {
            "state": "SAFE_EQUIVALENT",
            "code": "CREDENTIALS_NOT_MIGRATED",
            "detail": "auth.json holds provider credential material; Qubit "
                      "never copies secret values into the runtime home.",
            "qubit_counterpart": "CLI `qubit auth login --provider` "
                                 "(reference-only re-enrollment)",
        }
    else:
        credentials_audit = {"state": "ABSENT", "code": "",
                             "detail": "no auth.json in the source home"}
    if state_dir is not None and (state_dir / "profiles.json").is_file() \
            and str(state_dir / "profiles.json") not in restore_map.values():
        created_files.append(str(state_dir / "profiles.json"))
    cron_path = dest_home / "cron" / "jobs.json"
    cron_existed = cron_path.is_file()
    cron_result = _merge_cron(home, dest_home)
    if not cron_existed and cron_path.is_file():
        # B-04: rollback must remove a cron store the merge created.
        created_files.append(str(cron_path))
    if receipt_added:
        receipts_audit = (f"imported {len(receipt_added)} receipt/audit "
                          f"file(s) from the source home (skip-existing)")
    else:
        receipts_audit = ("no standalone receipt/audit store found in the "
                          "source home; action receipts are Qubit-owned "
                          "(actions.json) and were not fabricated")
    return {
        "model_profile": model_result,
        "added_files": added,
        "created_files": created_files,
        "cron": cron_result,
        "mcp": mcp_result,
        "skill_state": skill_state,
        "plugin_state": plugin_state,
        "restore_map": restore_map,
        "receipts_audit": receipts_audit,
        "aux_dbs": aux_dbs,
        "credentials_audit": credentials_audit,
    }


def plan(cfg: Any = None, *, home: Optional[Path] = None) -> Dict[str, Any]:
    inv = inspect(cfg, home=home)
    st = _dest_store(cfg)
    already = 0
    try:
        listing = st.list(limit=100, offset=0)
        already = int(listing.get("total") or 0)
    except Exception:
        already = 0
    counts = (inv.get("databases", {}).get("state.db", {}) or {}).get("counts", {})
    return {
        "schema": JOURNAL_SCHEMA,
        "state": "OK" if inv.get("state") == "OK" else inv.get("state"),
        "home": inv.get("home"),
        "source_sessions": int(counts.get("sessions") or 0),
        "source_messages": int(counts.get("messages") or 0),
        "destination": str(getattr(st, "_path", "")),
        "existing_dest_sessions": already,
        "secret_refs": inv.get("secret_refs", []),
        "catalog": catalog_report(cfg, home or source_home(cfg)),
        "dry_run": True,
    }


def _snapshot_sqlite(src: Path, dst: Path) -> None:
    """WAL-safe snapshot of a SQLite DB (fallback: copy db + -wal + -shm)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        sconn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dconn = sqlite3.connect(str(dst))
        with dconn:
            sconn.backup(dconn)
        sconn.close()
        dconn.close()
        return
    except Exception:  # noqa: BLE001 - fall back to a byte copy of sidecars
        pass
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(src) + suffix)
        if p.is_file():
            target = Path(str(dst) + suffix) if suffix else dst
            shutil.copy2(p, target)


def _backup_dir(cfg: Any, backup_dir: Optional[Path], home: Path) -> Path:
    if backup_dir is not None:
        return Path(backup_dir)
    base = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(base) / "hermes-migration" / stamp


def apply(cfg: Any = None, *, confirm: bool = False,
          backup_dir: Optional[Path] = None,
          home: Optional[Path] = None,
          safe: bool = False) -> Dict[str, Any]:
    """Import source sessions/messages. Dry-run unless ``confirm``.

    ``safe=True`` restricts the asset import to the non-secret subset: the
    reference provider/model profile and MCP registry are skipped (owner
    decision #9) while safe user-state stores (memories, checkpoints,
    channel_directory) are ported. Session/message import is unchanged.
    """
    home = Path(home) if home else source_home(cfg)
    state_db = home / "state.db"
    if not state_db.is_file():
        return {"schema": JOURNAL_SCHEMA, "state": "MISSING",
                "detail": f"no legacy agent state.db at {home}",
                "dry_run": not confirm}
    p = plan(cfg, home=home)
    if not confirm:
        p["state"] = "DRY_RUN"
        p["detail"] = ("dry run: pass confirm=true (CLI --apply) to import; "
                       "a timestamped backup is written first")
        return p

    st = _dest_store(cfg)
    bdir = _backup_dir(cfg, backup_dir, home)
    (bdir / "source").mkdir(parents=True, exist_ok=True)
    (bdir / "destination").mkdir(parents=True, exist_ok=True)
    # Back up every present source DB. B-04: use the SQLite backup API so a
    # live WAL source is captured consistently (a byte copy of just state.db
    # silently drops state.db-wal and the newest committed rows).
    for rel in _SOURCE_DBS:
        src = home / rel
        if src.is_file():
            _snapshot_sqlite(src, bdir / "source" / rel.replace("/", "__"))
    dest_path = Path(getattr(st, "_path", ""))
    # Consistent snapshot of the live SQLite store (WAL-safe) via the backup
    # API; fall back to a byte copy if the backup API is unavailable.
    if dest_path.is_file():
        try:
            src = sqlite3.connect(f"file:{dest_path}?mode=ro", uri=True)
            dst = sqlite3.connect(str(bdir / "destination" / dest_path.name))
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        except Exception:
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(dest_path) + suffix)
                if p.is_file():
                    shutil.copy2(p, bdir / "destination" / p.name)

    imported_sessions = 0
    skipped_sessions = 0
    imported_messages = 0
    skipped_roles = 0
    errors: List[str] = []
    imported_ids: List[str] = []
    conn = _connect_ro(state_db)
    try:
        for s in _source_sessions(conn):
            msgs, skipped = _source_messages(conn, s["id"])
            skipped_roles += skipped
            session = dict(s)
            # B-04 resume marker: lets a re-run detect and repair a partial
            # import instead of skipping it forever.
            session["expected_messages"] = len(msgs)
            payload = {
                "session": session,
                "messages": msgs,
                "system_prompts": _source_system_prompts(
                    conn, {str(s.get("system_prompt_hash") or "")}
                    if s.get("system_prompt_hash") else set()),
            }
            try:
                res = st.import_session(payload, overwrite=False,
                                        identity="migration:qubit")
            except Exception as exc:  # noqa: BLE001 - per-session, journaled
                errors.append(f"{s['id']}: {type(exc).__name__}")
                continue
            if res.get("state") == "EXISTS":
                skipped_sessions += 1
                continue
            if res.get("state") == "OK":
                imported_sessions += 1
                imported_messages += int(res.get("imported") or 0)
                imported_ids.append(s["id"])
            else:
                errors.append(f"{s['id']}: {res.get('code') or res.get('state')}")
    finally:
        conn.close()

    assets = _import_assets(cfg, home, bdir, safe=safe)

    journal = {
        "schema": JOURNAL_SCHEMA,
        "created_at": time.time(),
        "source_home": str(home),
        "destination": str(dest_path),
        "backup_dir": str(bdir),
        "safe": bool(safe),
        "imported_sessions": imported_sessions,
        "skipped_sessions": skipped_sessions,
        "imported_messages": imported_messages,
        "skipped_roles": skipped_roles,
        "secret_refs": p.get("secret_refs", []),
        "imported_ids": imported_ids,
        "errors": errors,
        "model_profile": assets["model_profile"],
        "added_files": assets["added_files"],
        "created_files": assets["created_files"],
        "cron": assets["cron"],
        "mcp": assets["mcp"],
        "skill_state": assets["skill_state"],
        "plugin_state": assets["plugin_state"],
        "catalog": catalog_report(cfg, home),
        "restore_map": assets["restore_map"],
        "receipts_audit": assets["receipts_audit"],
        "aux_dbs": assets["aux_dbs"],
        "credentials_audit": assets["credentials_audit"],
        "rollback": f"qubit product legacy-rollback --backup-dir {bdir} --apply",
    }
    (bdir / "journal.json").write_text(
        json.dumps(journal, indent=2, sort_keys=True), encoding="utf-8")
    log_event("migration.hermes.apply", imported_sessions=imported_sessions,
              skipped_sessions=skipped_sessions, imported_messages=imported_messages)
    return {"schema": JOURNAL_SCHEMA, "state": "OK" if not errors else "PARTIAL",
            "dry_run": False, **{k: journal[k] for k in (
                "imported_sessions", "skipped_sessions", "imported_messages",
                "skipped_roles", "secret_refs", "errors", "backup_dir",
                "destination",
                "model_profile", "cron", "mcp", "catalog", "receipts_audit",
                "aux_dbs", "credentials_audit", "skill_state", "plugin_state",
                "safe")},
            "added_files": len(journal["added_files"])}


def rollback(cfg: Any = None, *, backup_dir: Path,
             confirm: bool = False) -> Dict[str, Any]:
    """Restore the destination chat store from a migration backup."""
    bdir = Path(backup_dir)
    dest_dir = bdir / "destination"
    if not dest_dir.is_dir():
        return {"schema": JOURNAL_SCHEMA, "state": "MISSING",
                "detail": f"no destination backup under {bdir}"}
    candidates = sorted(dest_dir.glob("*"))
    if not candidates:
        return {"schema": JOURNAL_SCHEMA, "state": "MISSING",
                "detail": f"empty destination backup under {dest_dir}"}
    if not confirm:
        return {"schema": JOURNAL_SCHEMA, "state": "DRY_RUN",
                "detail": f"would restore {candidates[0].name}",
                "backup_dir": str(bdir)}
    st = _dest_store(cfg)
    dest_path = Path(getattr(st, "_path", ""))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # Close the cached writer before swapping files under it.
    from .qubit_session_store import close_cached_db

    close_cached_db(dest_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(dest_path) + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    # The DB snapshot is exactly the destination DB (and any sidecars) — never
    # an ``asset__`` backup.
    db_files = [f for f in candidates
                if not f.name.startswith("asset__")
                and (f.name == dest_path.name
                     or f.name.startswith(dest_path.name + "-"))]
    for f in db_files:
        target = dest_path.parent / f.name
        tmp = target.with_name(target.name + ".restore-tmp")
        shutil.copy2(f, tmp)
        os.replace(tmp, target)
    # Undo asset import: restore overwritten files, remove created/copied files.
    removed = restored_assets = 0
    jf = bdir / "journal.json"
    if jf.is_file():
        try:
            journal = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            journal = {}
        for backup_name, original in (journal.get("restore_map") or {}).items():
            backup_file = dest_dir / backup_name
            if not backup_file.is_file():
                continue
            target = Path(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".restore-tmp")
            shutil.copy2(backup_file, tmp)
            os.replace(tmp, target)
            restored_assets += 1
        for rel in list(journal.get("created_files") or []) \
                + list(journal.get("added_files") or []):
            try:
                Path(rel).unlink()
                removed += 1
            except (FileNotFoundError, IsADirectoryError, OSError):
                pass
    try:
        st.reload()
    except Exception:
        pass
    log_event("migration.hermes.rollback", backup_dir=str(bdir))
    return {"schema": JOURNAL_SCHEMA, "state": "OK",
            "restored": dest_path.name, "destination": str(dest_path),
            "removed_files": removed, "restored_assets": restored_assets,
            "backup_dir": str(bdir)}


# ---------------------------------------------------------------------------
# Sanitized CLI-history import
# ---------------------------------------------------------------------------
# Owner decision: the reference CLI history ``.hermes_history`` may contain
# pasted credentials (``sk-`` API keys). It must never be copied raw. This
# importer reads the source read-only, replaces every secret-looking value with
# a typed placeholder, emits a review report that carries counts and line
# numbers only, backs the destination up before writing, and can be rolled
# back. A final assertion refuses to write anything if the sanitized output
# still matches a secret pattern.
HISTORY_SCHEMA = "qubit-cli-history-import/1"
HISTORY_PLACEHOLDER = "«REDACTED:SECRET»"

# Conservative, explicit credential shapes. ``kind`` selects the replacement:
# ``value`` masks the whole match; ``keyvalue`` keeps ``key=``/``key:`` and any
# opening quote; ``bearer`` keeps the scheme word. Patterns are word-bounded and
# length-floored so ordinary prose is never masked.
_HISTORY_SECRET_PATTERNS: tuple = (
    ("openai_sk", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{11,}\b"), "value"),
    ("stripe_secret", re.compile(r"\bsk_live_[A-Za-z0-9]{10,}\b"), "value"),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "value"),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "value"),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "value"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "value"),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
     "value"),
    ("private_key_block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "value"),
    ("bearer", re.compile(
        r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=+\-]{8,})"), "bearer"),
    ("key_value", re.compile(
        r"(?i)\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
        r"auth[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|"
        r"pwd|secret|token)\b(\s*[=:]\s*)(\"?)([^\s\"'&,;]+)"), "keyvalue"),
)

# An unkeyed opaque token: base64/hex-ish, length-floored, at least three
# character classes (lower + upper + digit) and Shannon entropy >= 3.5
# bits/char. Plain lowercase hex (a git SHA) and single-case path fragments are
# deliberately excluded, so safe history lines keep working.
_HISTORY_ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9_+=\-]{32,}")
_HISTORY_ENTROPY_MIN = 3.5


def _shannon_entropy(token: str) -> float:
    import math
    from collections import Counter

    if not token:
        return 0.0
    counts = Counter(token)
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _high_entropy_token(token: str) -> bool:
    """True for an opaque credential-shaped token, never for ordinary prose."""
    if len(token) < 32:
        return False
    if not (re.search(r"[a-z]", token) and re.search(r"[A-Z]", token)
            and re.search(r"[0-9]", token)):
        return False
    if len(set(token)) < 12:
        return False
    return _shannon_entropy(token) >= _HISTORY_ENTROPY_MIN


def _apply_history_pattern(rx: "re.Pattern[str]", line: str, repl) -> tuple:
    """Apply ``rx`` counting only replacements that actually changed text."""
    count = 0

    def _wrapped(match):
        nonlocal count
        try:
            result = repl(match)
        except Exception:  # noqa: BLE001 - a sanitizer never aborts the import
            return match.group(0)
        if result != match.group(0):
            count += 1
        return result

    return rx.sub(_wrapped, line), count


def _sanitize_history_line(line: str) -> tuple:
    """Return ``(sanitized_line, count, {pattern_name: count})`` for one line."""
    out = line
    counts: Dict[str, int] = {}
    total = 0
    for name, rx, kind in _HISTORY_SECRET_PATTERNS:
        if kind == "keyvalue":
            def _repl(m):
                if m.group(4) == HISTORY_PLACEHOLDER:
                    return m.group(0)
                return (f"{m.group(1)}{m.group(2)}{m.group(3)}"
                        f"{HISTORY_PLACEHOLDER}")
        elif kind == "bearer":
            def _repl(m):
                if m.group(2) == HISTORY_PLACEHOLDER:
                    return m.group(0)
                return f"{m.group(1)} {HISTORY_PLACEHOLDER}"
        else:
            def _repl(m):
                return HISTORY_PLACEHOLDER

        out, n = _apply_history_pattern(rx, out, _repl)
        if n:
            counts[name] = counts.get(name, 0) + n
            total += n

    def _entropy_repl(m):
        return (HISTORY_PLACEHOLDER if _high_entropy_token(m.group(0))
                else m.group(0))

    out, n = _apply_history_pattern(_HISTORY_ENTROPY_TOKEN, out, _entropy_repl)
    if n:
        counts["high_entropy_token"] = counts.get("high_entropy_token", 0) + n
        total += n
    return out, total, counts


def sanitize_cli_history(text: str) -> Dict[str, Any]:
    """Sanitize a CLI-history blob; the report never carries secret text.

    Returns the sanitized ``text`` plus a review report: the total
    ``redactions``, the 1-based ``redacted_lines``, the source ``lines``, and
    per-pattern ``patterns`` counts. Re-running the sanitizer on its own output
    produces zero redactions (idempotent).
    """
    raw = text if isinstance(text, str) else str(text)
    lines = raw.splitlines()
    clean: List[str] = []
    redacted_lines: List[int] = []
    patterns: Dict[str, int] = {}
    total = 0
    for idx, line in enumerate(lines, 1):
        out_line, n, counts = _sanitize_history_line(line)
        if n:
            redacted_lines.append(idx)
            total += n
            for name, c in counts.items():
                patterns[name] = patterns.get(name, 0) + c
        clean.append(out_line)
    trailer = "\n" if raw.endswith("\n") else ""
    return {
        "schema": HISTORY_SCHEMA,
        "text": "\n".join(clean) + trailer,
        "lines": len(lines),
        "redactions": total,
        "redacted_lines": redacted_lines,
        "patterns": patterns,
    }


def _history_source_path(home: Path) -> Path:
    return Path(home) / ".hermes_history"


def _history_dest_path(cfg: Any, dest: Optional[Path]) -> Path:
    if dest is not None:
        return Path(dest)
    return _qubit_home(cfg) / ".hermes_history"


def _history_backup_dir(cfg: Any, backup_dir: Optional[Path]) -> Path:
    if backup_dir is not None:
        return Path(backup_dir)
    base = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(base) / "hermes-migration" / f"history-{stamp}"


def _write_history_review(review: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review, indent=2, sort_keys=True),
                    encoding="utf-8")


def import_cli_history_sanitized(
        cfg: Any = None, *, home: Optional[Path] = None,
        dest: Optional[Path] = None, confirm: bool = False,
        backup_dir: Optional[Path] = None,
        review_path: Optional[Path] = None) -> Dict[str, Any]:
    """Sanitized, reviewable, reversible import of the CLI history.

    Dry-run/review by default: nothing is written unless ``confirm=True``. The
    source is read read-only; secrets are replaced by
    :data:`HISTORY_PLACEHOLDER`; a backup of any existing destination is
    written under a timestamped directory; and the write is refused if the
    sanitized output still matches a secret pattern. A re-run adds only new
    lines (idempotent). ``review_path`` writes the review report (counts and
    line numbers only) to a file.
    """
    src = _history_source_path(Path(home) if home else source_home(cfg))
    dst = _history_dest_path(cfg, dest)
    review: Dict[str, Any] = {
        "schema": HISTORY_SCHEMA,
        "source": str(src),
        "destination": str(dst),
        "source_present": src.is_file(),
        "source_lines": 0,
        "source_redacted_lines": [],
        "redactions": 0,
        "patterns": {},
        "existing_lines": 0,
        "added_lines": 0,
        "destination_existed": dst.is_file(),
        "dry_run": not confirm,
    }
    if not src.is_file():
        review.update(state="MISSING",
                      detail=f"no reference CLI history at {src}")
        return review
    try:
        raw = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        review.update(state="UNREADABLE",
                      detail=f"source unreadable ({type(exc).__name__}); "
                             f"nothing written")
        return review

    sanitized = sanitize_cli_history(raw)
    review["source_lines"] = sanitized["lines"]
    review["source_redacted_lines"] = sanitized["redacted_lines"]
    review["source_redactions"] = sanitized["redactions"]
    review["redactions"] = sanitized["redactions"]
    review["patterns"] = sanitized["patterns"]

    existing: List[str] = []
    if dst.is_file():
        try:
            existing = dst.read_text(encoding="utf-8",
                                     errors="replace").splitlines()
        except OSError as exc:
            review.update(
                state="UNREADABLE",
                detail=f"destination unreadable ({type(exc).__name__}); "
                       f"nothing written")
            return review
    review["existing_lines"] = len(existing)

    # The pre-existing destination is sanitized too: a secret already present
    # in Qubit's own history must never be preserved by the import. Then the
    # merged text is the exact bytes about to be written.
    existing_text = ("\n".join(existing) + "\n") if existing else ""
    existing_sanitized = sanitize_cli_history(existing_text)
    review["destination_redacted_lines"] = existing_sanitized["redacted_lines"]
    review["destination_redactions"] = existing_sanitized["redactions"]
    review["destination_patterns"] = existing_sanitized["patterns"]

    # De-duplicate incoming lines against the *sanitized* existing lines (the
    # exact lines that will be kept). Comparing against the raw destination
    # would re-add a line whose secret was already redacted there, duplicating
    # it whenever the same secret appears in both source and destination.
    seen = set(existing_sanitized["text"].splitlines())
    incoming = [ln for ln in sanitized["text"].splitlines() if ln not in seen]
    review["added_lines"] = len(incoming)
    review["redactions"] = (sanitized["redactions"]
                            + existing_sanitized["redactions"])
    final_lines = existing_sanitized["text"].splitlines() + incoming
    merged = "\n".join(final_lines)
    if final_lines:
        merged += "\n"

    # Fail closed: the text about to be written must contain zero secrets. A
    # match here means the sanitizer missed a shape, so we write nothing.
    if sanitize_cli_history(merged)["redactions"]:
        review.update(
            state="REFUSED", code="HISTORY_SECRET_ASSERT_FAILED",
            detail="sanitized output still matched a secret pattern; nothing "
                   "written")
        return review

    if not confirm:
        review.update(state="DRY_RUN",
                      detail="review only: no file written; pass --apply to "
                             "write after a backup")
        if review_path is not None:
            _write_history_review(review, Path(review_path))
            review["review_path"] = str(review_path)
        return review

    if not incoming and not existing_sanitized["redactions"]:
        review.update(state="NOOP",
                      detail="destination already contains every history line; "
                             "nothing written")
        if review_path is not None:
            _write_history_review(review, Path(review_path))
            review["review_path"] = str(review_path)
        return review

    bdir = _history_backup_dir(cfg, backup_dir)
    (bdir / "destination").mkdir(parents=True, exist_ok=True)
    if dst.is_file():
        shutil.copy2(dst, bdir / "destination" / dst.name)
    review["backup_dir"] = str(bdir)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".qubit-tmp")
    tmp.write_text(merged, encoding="utf-8")
    os.replace(tmp, dst)

    review.update(state="OK")
    journal = dict(review)
    journal["created_at"] = time.time()
    (bdir / "journal.json").write_text(
        json.dumps(journal, indent=2, sort_keys=True), encoding="utf-8")
    _write_history_review(journal, bdir / "review.json")
    if review_path is not None:
        _write_history_review(journal, Path(review_path))
        review["review_path"] = str(review_path)
    log_event("migration.history.import", added_lines=len(incoming),
              redactions=review["redactions"])
    return {"schema": HISTORY_SCHEMA, "state": "OK", "dry_run": False, **review}


def rollback_cli_history(cfg: Any = None, *, backup_dir: Path,
                         confirm: bool = False) -> Dict[str, Any]:
    """Restore the pre-import destination from a history-import backup.

    Refuses (``HISTORY_NO_BACKUP``) when there is no journal, or when the
    journal says the destination existed but no backup copy is present. A
    destination the import created is removed. Dry-run unless ``confirm``.
    """
    bdir = Path(backup_dir)
    journal_f = bdir / "journal.json"
    if not journal_f.is_file():
        return {"schema": HISTORY_SCHEMA, "state": "MISSING",
                "code": "HISTORY_NO_BACKUP",
                "detail": f"no history-import journal under {bdir}"}
    try:
        journal = json.loads(journal_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": HISTORY_SCHEMA, "state": "MISSING",
                "code": "HISTORY_BACKUP_UNREADABLE",
                "detail": f"history-import journal unreadable under {bdir}"}
    dst = Path(str(journal.get("destination") or ""))
    if not str(dst):
        return {"schema": HISTORY_SCHEMA, "state": "MISSING",
                "code": "HISTORY_BACKUP_INVALID",
                "detail": "journal records no destination"}
    existed = bool(journal.get("destination_existed"))
    backup_file = bdir / "destination" / dst.name
    if existed and not backup_file.is_file():
        return {"schema": HISTORY_SCHEMA, "state": "MISSING",
                "code": "HISTORY_NO_BACKUP",
                "detail": "journal says the destination existed but no backup "
                          "copy was found; refusing to guess"}
    if not confirm:
        return {"schema": HISTORY_SCHEMA, "state": "DRY_RUN",
                "detail": (f"would restore {backup_file}" if existed
                           else f"would remove created {dst}"),
                "destination": str(dst), "backup_dir": str(bdir)}
    if existed:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".restore-tmp")
        shutil.copy2(backup_file, tmp)
        os.replace(tmp, dst)
        action = "restored"
    else:
        try:
            dst.unlink()
            action = "removed"
        except FileNotFoundError:
            action = "noop"
    log_event("migration.history.rollback", backup_dir=str(bdir),
              action=action)
    return {"schema": HISTORY_SCHEMA, "state": "OK", "action": action,
            "destination": str(dst), "backup_dir": str(bdir)}
