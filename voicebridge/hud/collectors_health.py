"""HUD health collector — Qubit-native port of ``backend/collectors/health.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package. Rebranded and re-pointed at
QUBIT state.

What changed from upstream, and why
-----------------------------------
* **Home** — upstream read ``default_hermes_dir()`` (``HERMES_HOME`` or the
  legacy dot-home). Every path here resolves from
  :func:`qubit_constants.get_qubit_home`, so profiles stay independent islands.
* **CLI probe removed** — upstream shelled ``hermes --version`` and made the
  Chat/Cron features depend on its exit code. Qubit owns its own binary; the
  version is read from ``VERSION`` beside the checkout, and the diagnostic
  reports the running interpreter/entry-point instead of spawning a process.
* **state.db diagnostics** — upstream opened ``state.db`` with a second
  writer-capable connection and ``PRAGMA table_info`` on every table. This
  module never opens the database: the session/cost counters and the
  schema/column checks come from the ONE chat-session authority
  (:class:`voicebridge.qubit_session_store.QubitSessionStore`) and its
  documented read model. A ``PRAGMA table_info`` check is replaced by the
  authority's own ``analytics_usage`` capability probe, so a schema the
  authority cannot read surfaces as a broken diagnostic instead of a silently
  blank tab.
* **Services** — upstream probed ``gateway.pid`` + ``systemctl is-active
  hermes-gateway`` + ``llama-server``. Qubit publishes gateway liveness in
  ``<home>/state/gateway.heartbeat`` and the local brain in an HTTP loopback
  port; both are read directly (no shelling).
* **Features** — dropped the Sudo, Providers and Gateway feature rows whose
  upstream implementations belong to tabs this package does not port (see
  ``voicebridge/hud/__init__.py`` SKIP lines). Chat/Sessions/Model Analytics/
  Plugins/Cron remain, wired to Qubit's own dependencies.
* **Schema baseline** — upstream compared ``state.db`` against the *agent*
  schema versions it was verified on. That baseline is meaningless for Qubit's
  store, so it is replaced with the authority's contract check.

Returns a plain ``dict`` (JSON-ready). Never raises: a collector that cannot
read a source reports it as a ``broken``/``warning`` diagnostic.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Directories created by Qubit under its home. ``(name, relpath, missing_status)``.
#: ``missing_status`` keeps a fresh install from being reported as broken.
_EXPECTED_DIRS = (
    ("Qubit Home", "", "broken"),
    ("Config", "config.yaml", "warning"),
    ("Environment", ".env", "warning"),
    ("State Database", "state.db", "broken"),
    ("Logs", "logs", "warning"),
    ("Model Cache", "models_dev_cache.json", "warning"),
    ("Plugins", "plugins", "warning"),
    ("Skills", "skills", "warning"),
)

#: ``(label, relpath, warning_after_s, broken_after_s)`` freshness probes.
_FRESHNESS = (
    ("state.db", "state.db", 3600, 86400),
    ("models_dev_cache.json", "models_dev_cache.json", 86400 * 7, 86400 * 30),
)

#: Data-layout moves Qubit follows from the pre-rename layout. Flags only when
#: the OLD path exists while its replacement is absent.
_LAYOUT_MIGRATIONS = (
    ("memory", "memory", "memories"),
    ("cron jobs", "jobs.json", "cron/jobs.json"),
)

#: Provider credential env-var groups, in provider-registry order. Each entry is
#: ``(provider_id, label, env_vars)`` — the same names the vendored provider
#: registry declares, so presence here means Qubit can actually reach that
#: provider. Only NAMES are read; no value is returned, logged, or counted.
_EXPECTED_KEYS = (
    ("anthropic", "Anthropic", ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN")),
    ("openrouter", "OpenRouter", ("OPENROUTER_API_KEY",)),
    ("openai-api", "OpenAI API", ("OPENAI_API_KEY",)),
    ("gemini", "Google AI Studio", ("GOOGLE_API_KEY", "GEMINI_API_KEY")),
    ("xai", "xAI", ("XAI_API_KEY",)),
    ("deepseek", "DeepSeek", ("DEEPSEEK_API_KEY",)),
    ("minimax", "MiniMax", ("MINIMAX_API_KEY",)),
    ("opencode-go", "OpenCode Go", ("OPENCODE_GO_API_KEY",)),
    ("opencode-zen", "OpenCode Zen", ("OPENCODE_ZEN_API_KEY",)),
    ("zai", "Z.AI / GLM", ("GLM_API_KEY", "ZAI_API_KEY")),
    ("kimi-coding", "Kimi / Moonshot", ("KIMI_API_KEY", "KIMI_CODING_API_KEY")),
    ("nous", "Qubit Labs", ("NOUS_API_KEY",)),
)

#: Vendor tokens checked for presence only (never surfaced, never sent).
_EXPECTED_PLATFORM_TOKENS = (
    ("TELEGRAM_BOT_TOKEN", "Telegram gateway bot token"),
)

_AUTO_DISCOVER_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")

#: Loopback endpoint the local-brain mode listens on (llama.cpp server / LM Studio).
_LOCAL_BRAIN_PORTS = (1234, 8080)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


def _qubit_home(home: Optional[Path]) -> Path:
    """Active Qubit home. Explicit argument wins; else the vendored resolver."""
    if home is not None:
        return Path(home)
    try:
        from qubit_constants import get_qubit_home  # type: ignore
    except Exception:  # pragma: no cover - vendored tree not importable
        _ensure_vendored()
        from qubit_constants import get_qubit_home  # type: ignore
    return Path(get_qubit_home())


def _ensure_vendored() -> None:
    """Put the vendored agent core on ``sys.path`` (idempotent)."""
    from .. import qubit_runtime

    qubit_runtime.ensure_path()


def _qubit_version() -> str:
    """Version of the running Qubit build, read from the checkout ``VERSION``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
    return ""


def _dotenv_keys(paths: List[Path]) -> set:
    """Key NAMES declared in ``.env`` files (values are never read out)."""
    keys: set = set()
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name = line.split("=", 1)[0].strip()
                    if name and name.replace("_", "").isalnum():
                        keys.add(name)
        except OSError:
            continue
    return keys


def _diag(name: str, status: str, detail: str, category: str,
          depends_on: Optional[List[str]] = None, suggested_fix: str = "",
          actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,  # ok | warning | broken
        "detail": detail,
        "category": category,
        "depends_on": list(depends_on or []),
        "suggested_fix": suggested_fix,
        "actions": list(actions or [{"name": "Recheck", "kind": "refresh"}]),
    }


def _file_diag(name: str, path: Path, category: str,
               missing_status: str = "warning") -> Dict[str, Any]:
    if not path.exists():
        return _diag(name, missing_status, f"{path} missing", category,
                     depends_on=[str(path)],
                     suggested_fix=f"Create or restore {path}.")
    if os.access(path, os.R_OK):
        return _diag(name, "ok", f"{path} readable", category,
                     depends_on=[str(path)])
    return _diag(name, "broken", f"{path} not readable", category,
                 depends_on=[str(path)],
                 suggested_fix=f"Fix read permissions for {path}.")


def _freshness_diag(name: str, path: Path, warning_after: int,
                    broken_after: int) -> Dict[str, Any]:
    if not path.exists():
        return _diag(name, "warning", f"{path} missing", "freshness",
                     depends_on=[str(path)],
                     suggested_fix=f"Generate or restore {path}.")
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _diag(name, "broken", f"{path} stat failed", "freshness",
                     depends_on=[str(path)])
    age = int(time.time() - mtime)
    status = "broken" if age > broken_after else (
        "warning" if age > warning_after else "ok")
    return {
        "name": name,
        "status": status,
        "detail": f"updated {age}s ago",
        "category": "freshness",
        "updated_at": _iso(datetime.fromtimestamp(mtime)),
        "age_seconds": age,
        "depends_on": [str(path)],
        "suggested_fix": ("Recheck after Qubit writes fresh data."
                          if status != "ok" else ""),
        "actions": [{"name": "Recheck", "kind": "refresh"}],
    }


def _layout_diag(home: Path) -> Dict[str, Any]:
    drifted = []
    for label, old_rel, new_rel in _LAYOUT_MIGRATIONS:
        if (home / old_rel).exists() and not (home / new_rel).exists():
            drifted.append(f"{label}: found {old_rel}, expected {new_rel}")
    if drifted:
        return _diag("Qubit data layout", "warning", "; ".join(drifted),
                     "readiness", depends_on=["Qubit Home"],
                     suggested_fix="Move the legacy paths into their current "
                                   "locations, then recheck.")
    return _diag("Qubit data layout", "ok", "matches the current layout",
                 "readiness", depends_on=["Qubit Home"])


def _feature_diag(name: str, ok: bool, detail: str, warn: bool = False,
                  depends_on: Optional[List[str]] = None,
                  suggested_fix: str = "",
                  actions: Optional[List[Dict[str, Any]]] = None
                  ) -> Dict[str, Any]:
    status = "ok" if ok else ("warning" if warn else "broken")
    return _diag(name, status, detail, "features", depends_on=depends_on,
                 suggested_fix=suggested_fix, actions=actions)


def _sort_diagnostics(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rank = {"broken": 0, "warning": 1, "ok": 2}
    return sorted(items, key=lambda i: (rank.get(i["status"], 3),
                                        i["name"].lower()))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@dataclass
class _SessionFacts:
    """What the session authority can tell health, without a second DB opener."""

    available: bool = False
    detail: str = ""
    session_count: int = 0
    last_session_at: Optional[datetime] = None
    analytics_ok: bool = False
    analytics_detail: str = ""
    totals: Dict[str, Any] = field(default_factory=dict)


def _authority_db_path(cfg: Any) -> Optional[Path]:
    """Resolve (read-only, no connection) the ``state.db`` the authority reads.

    ``qubit_session_store.qubit_state_db_path`` is the authority's own path
    resolver; it is called with no store construction, so asking where the data
    lives never opens the database.
    """
    try:
        _ensure_vendored()
        from ..qubit_session_store import qubit_state_db_path

        return Path(qubit_state_db_path(cfg))
    except Exception:  # noqa: BLE001 - unknown path is reported as "the home's"
        return None


def _session_facts(cfg: Any) -> _SessionFacts:
    """Read-only session facts through the ONE chat-session authority.

    ``QubitSessionStore`` is the named authority (``CHAT_SESSION_AUTHORITY``);
    its ``list``/``reload``/``analytics_usage`` reads are the documented read
    model. Nothing here opens ``state.db`` itself.
    """
    facts = _SessionFacts()
    try:
        from ..qubit_session_store import QubitSessionStore, analytics_usage
    except Exception as exc:  # noqa: BLE001 - a fork miss is a diagnostic
        facts.detail = f"session authority unavailable ({exc})"
        return facts
    try:
        store = QubitSessionStore(cfg)
        facts.available = True
        reloaded = store.reload() or {}
        facts.session_count = int(reloaded.get("count") or 0)
        facts.detail = str(reloaded.get("state") or "OK")
        newest = store.list(limit=1, include_archived=True) or {}
        rows = newest.get("sessions") or []
        if rows:
            stamp = rows[0].get("last_active") or rows[0].get("started_at")
            if stamp:
                facts.last_session_at = datetime.fromtimestamp(float(stamp))
    except Exception as exc:  # noqa: BLE001
        facts.available = False
        facts.detail = f"session read failed ({exc})"
        return facts
    try:
        usage = analytics_usage(cfg, days=30)
    except Exception as exc:  # noqa: BLE001
        facts.analytics_detail = f"usage aggregate failed ({exc})"
        return facts
    if usage is None:
        facts.analytics_detail = "usage aggregate unavailable"
        return facts
    facts.analytics_ok = True
    facts.totals = dict(usage.get("totals") or {})
    facts.analytics_detail = (f"{len(usage.get('by_model') or [])} model(s), "
                              f"{len(usage.get('daily') or [])} day(s) of usage")
    return facts


def _gateway_heartbeat(home: Path) -> Dict[str, Any]:
    """Gateway liveness from the file Qubit's gateway daemon publishes."""
    path = home / "state" / "gateway.heartbeat"
    if not path.exists():
        return {"name": "Gateway", "running": False, "pid": None,
                "note": "no heartbeat file"}
    try:
        age = int(time.time() - path.stat().st_mtime)
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return {"name": "Gateway", "running": False, "pid": None,
                "note": "heartbeat unreadable"}
    pid: Optional[int] = None
    for token in raw.replace(",", " ").split():
        if token.isdigit():
            pid = int(token)
            break
    running = age <= 180
    return {"name": "Gateway", "running": running, "pid": pid,
            "note": f"heartbeat {age}s ago" if running
                    else f"heartbeat stale ({age}s)"}


def _local_brain() -> Dict[str, Any]:
    """Local-brain server liveness: a loopback connect probe, never a shell-out.

    A refused connection means nothing is listening; any process listening on
    the local inference port is the local brain.
    """
    import socket

    for port in _LOCAL_BRAIN_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return {"name": "Local brain", "running": True, "pid": None,
                        "note": f"listening on 127.0.0.1:{port}"}
        except OSError:
            continue
    return {"name": "Local brain", "running": False, "pid": None,
            "note": "not listening"}


def _credential_keys(home: Path, cfg: Any) -> List[Dict[str, Any]]:
    """Credential PRESENCE per provider — names only, never a value.

    A provider counts as configured when at least one of its env names is set in
    the process environment, declared in a ``.env`` file, or present in the
    redacted credential pool (:func:`voicebridge.credentials.inventory`).
    """
    dotenv = _dotenv_keys([home / ".env", Path.home() / ".env"])
    pooled: set = set()
    try:
        from .. import credentials

        inv = credentials.inventory(cfg) or {}
        pooled = {str(p.get("provider") or "") for p in inv.get("providers") or []
                  if int(p.get("count") or 0) > 0}
    except Exception:  # noqa: BLE001 - pool unreadable is simply "not pooled"
        pooled = set()

    def _present(names) -> bool:
        return any(name in os.environ or name in dotenv for name in names)

    keys: List[Dict[str, Any]] = []
    for provider_id, label, names in _EXPECTED_KEYS:
        present = _present(names) or provider_id in pooled
        keys.append({
            "name": names[0],
            "provider": provider_id,
            "label": label,
            "source": "env" if present else "",
            "present": present,
            "note": "" if present else f"{label} not configured",
        })
    for name, note in _EXPECTED_PLATFORM_TOKENS:
        present = _present((name,))
        keys.append({"name": name, "provider": "telegram", "label": "Telegram",
                     "source": "env" if present else "", "present": present,
                     "note": "" if present else note})

    known = ({n for _p, _l, names in _EXPECTED_KEYS for n in names}
             | {n for n, _ in _EXPECTED_PLATFORM_TOKENS})
    for extra in sorted(dotenv):
        if extra in known or not extra.endswith(_AUTO_DISCOVER_SUFFIXES):
            continue
        if extra in os.environ or extra in dotenv:
            keys.append({"name": extra, "provider": "", "label": "",
                         "source": "env", "present": True,
                         "note": "discovered"})
    return keys


def _config_summary(home: Path) -> Dict[str, str]:
    """``model``/``provider`` from Qubit's own ``config.yaml`` (read-only)."""
    path = home / "config.yaml"
    out = {"model": "", "provider": ""}
    if not path.is_file():
        return out
    try:
        import yaml  # noqa: PLC0415 - pyyaml is a declared dependency

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable config is not a crash
        return out
    section = data.get("model") if isinstance(data, dict) else None
    if isinstance(section, str):
        out["model"] = section.strip()
    elif isinstance(section, dict):
        out["model"] = str(section.get("default") or section.get("model") or "")
        out["provider"] = str(section.get("provider") or "")
    return out


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def collect_health(home: Optional[Path] = None, cfg: Any = None) -> Dict[str, Any]:
    """Collect Qubit health.

    Pure function: reads Qubit's files and the session authority, returns a
    JSON-ready dict. ``cfg`` is the VoiceBridge config threaded to the session
    authority so a profile resolves its own island; ``home`` overrides the
    resolved Qubit home (tests, profile views).

    Session/usage counters come from the ONE chat-session authority for the
    *active* home; an explicit ``home`` that is not the home the authority
    resolves would otherwise report another island's database, so the file
    diagnostics use ``home`` while the counters always follow the authority.
    """
    root = _qubit_home(home)
    state_db = root / "state.db"

    # The session authority answers for ITS home (``QUBIT_HOME`` / the profile
    # island), which may be a different directory from an explicit ``home``
    # argument. Which store is actually being read is reported below, so the
    # filesystem rows here never claim to describe a store the authority reads.
    authority_db = _authority_db_path(cfg)
    session_db = authority_db or state_db

    readiness: List[Dict[str, Any]] = []
    for name, relpath, missing in _EXPECTED_DIRS:
        path = root / relpath if relpath else root
        if relpath == "state.db" and authority_db is not None:
            path = authority_db
            name = "State Database"
        readiness.append(_file_diag(name, path, "readiness", missing))
    readiness.append(_layout_diag(root))

    version = _qubit_version()
    cli_status = "ok" if version else "warning"
    readiness.append(_diag(
        "Qubit build", cli_status,
        f"Qubit {version}" if version else "VERSION not found beside the checkout",
        "readiness",
        suggested_fix="" if version else "Run from a full checkout or install.",
    ))

    freshness: List[Dict[str, Any]] = [
        _freshness_diag(name, root / relpath, warn, broken)
        for name, relpath, warn, broken in _FRESHNESS
    ]

    facts = _session_facts(cfg)
    database: List[Dict[str, Any]] = []
    if facts.available:
        database.append(_diag(
            "session authority", "ok",
            f"{facts.session_count} session(s); {facts.detail}"
            + (f"; store {session_db}" if authority_db else ""),
            "database", depends_on=[str(session_db)],
        ))
    else:
        database.append(_diag(
            "session authority", "broken", facts.detail, "database",
            depends_on=["state.db"],
            suggested_fix="Restore the Qubit state database or its schema.",
        ))
    database.append(_diag(
        "usage aggregate",
        "ok" if facts.analytics_ok else "broken",
        facts.analytics_detail or "not read", "database",
        depends_on=["state.db", "sessions"],
        suggested_fix="" if facts.analytics_ok else
        "The session authority could not aggregate usage; verify the sessions "
        "table is readable.",
    ))

    if facts.last_session_at:
        age = int(time.time() - facts.last_session_at.timestamp())
        freshness.append({
            "name": "last session",
            "status": "ok" if age <= 86400 * 7 else "warning",
            "detail": f"{facts.session_count} session(s); latest {age}s ago",
            "category": "freshness",
            "updated_at": _iso(facts.last_session_at),
            "age_seconds": age,
            "depends_on": ["sessions"],
            "suggested_fix": "",
            "actions": [{"name": "Recheck", "kind": "refresh"}],
        })
    else:
        freshness.append(_diag(
            "last session", "warning", "no sessions recorded", "freshness",
            depends_on=["sessions"],
            suggested_fix="Start a Qubit session, then recheck health.",
            actions=[{"name": "Open Chat", "kind": "tab", "target": "chat"},
                     {"name": "Recheck", "kind": "refresh"}],
        ))

    config = _config_summary(root)
    keys = _credential_keys(root, cfg)
    keys_present = sum(1 for k in keys if k["present"])

    services = [
        _gateway_heartbeat(root),
        _local_brain(),
    ]

    db_ok = facts.available
    usage_ok = facts.analytics_ok
    config_ok = bool(config["model"] and config["provider"])
    plugins_ok = (root / "plugins").is_dir()
    skills_ok = (root / "skills").is_dir()
    gateway_ok = any(s["running"] for s in services
                     if s["name"] == "Gateway")

    features = [
        _feature_diag(
            "Chat", config_ok and db_ok,
            "requires config, the session authority and the state database",
            depends_on=["Config", "session authority", "State Database"],
            suggested_fix="Fix the broken dependency above, then retry chat.",
            actions=[{"name": "Open Chat", "kind": "tab", "target": "chat"},
                     {"name": "Recheck", "kind": "refresh"}],
        ),
        _feature_diag(
            "Sessions", db_ok, "requires the session authority",
            depends_on=["state.db", "session authority"],
            suggested_fix="Restore or migrate state.db so sessions are readable.",
            actions=[{"name": "Open Sessions", "kind": "tab", "target": "sessions"},
                     {"name": "Recheck", "kind": "refresh"}],
        ),
        _feature_diag(
            "Model Analytics", db_ok and usage_ok,
            "requires the usage aggregate over the sessions table",
            depends_on=["state.db", "usage aggregate"],
            suggested_fix="Verify the sessions table exposes token/cost columns.",
            actions=[{"name": "Open Models", "kind": "tab", "target": "model-info"},
                     {"name": "Recheck", "kind": "refresh"}],
        ),
        _feature_diag(
            "Skills", skills_ok, "requires the skills directory",
            warn=True, depends_on=["skills"],
            suggested_fix="Create the Qubit skills directory or install a skill.",
            actions=[{"name": "Open Skills", "kind": "tab", "target": "skills"},
                     {"name": "Recheck", "kind": "refresh"}],
        ),
        _feature_diag(
            "Plugins", plugins_ok, "requires the plugins directory",
            warn=True, depends_on=["plugins"],
            suggested_fix="Create the Qubit plugins directory or install a plugin.",
            actions=[{"name": "Open Plugins", "kind": "tab", "target": "plugins"},
                     {"name": "Recheck", "kind": "refresh"}],
        ),
        _feature_diag(
            "Cron", db_ok, "requires the session authority for job history",
            warn=not db_ok, depends_on=["state.db", "cron"],
            suggested_fix="Verify the scheduler store is present.",
            actions=[{"name": "Open Cron", "kind": "tab", "target": "cron"},
                     {"name": "Recheck", "kind": "refresh"}],
        ),
        _feature_diag(
            "Gateway", gateway_ok, "uses the gateway heartbeat",
            warn=not gateway_ok, depends_on=["state/gateway.heartbeat"],
            suggested_fix="Start the gateway, then recheck health.",
            actions=[{"name": "Recheck", "kind": "refresh"}],
        ),
    ]

    readiness = _sort_diagnostics(readiness)
    freshness = _sort_diagnostics(freshness)
    database = _sort_diagnostics(database)
    features = _sort_diagnostics(features)
    all_diags = readiness + freshness + database + features

    return {
        "qubit_home": str(root),
        "qubit_home_exists": root.exists(),
        "qubit_version": version,
        # The store the authority actually reads (may differ from ``home`` when
        # an explicit home was passed); ``None`` = the home argument's own file.
        "session_store": str(authority_db) if authority_db else None,
        "state_db_exists": session_db.exists(),
        "state_db_size": (session_db.stat().st_size
                          if session_db.exists() else 0),
        "config_model": config["model"],
        "config_provider": config["provider"],
        "session_count": facts.session_count,
        "last_session_at": _iso(facts.last_session_at),
        "usage_totals": facts.totals,
        "keys": keys,
        "keys_ok": keys_present,
        "keys_missing": len(keys) - keys_present,
        "services": services,
        "services_ok": sum(1 for s in services if s["running"]),
        "readiness": readiness,
        "freshness": freshness,
        "database": database,
        "features": features,
        "diagnostics_ok": sum(1 for d in all_diags if d["status"] == "ok"),
        "diagnostics_warnings": sum(1 for d in all_diags
                                    if d["status"] == "warning"),
        "diagnostics_broken": sum(1 for d in all_diags
                                  if d["status"] == "broken"),
        "all_healthy": (keys_present == len(keys)
                        and all(s["running"] for s in services)),
        "collected_at": _iso(datetime.now()),
    }


__all__ = ["collect_health"]
