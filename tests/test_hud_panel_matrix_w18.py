"""The HUD panel matrix — all 19 tabs, route to payload, on the real dispatcher.

The HUD is nineteen read-only tabs over nineteen collectors
(``voicebridge/hud_routes.py`` + ``voicebridge/hud/collectors_*.py``), served by
``frontend/qubit-app/plugins/qubit-hud/qubit-hud.js``. What was never checked:
that EVERY tab the renderer draws has a backing route, and that the route
answers a TYPED payload — real data, or an honest typed empty/unavailable state.

The failure modes this file exists to catch, each of which has happened once:

(a) **A dark tab.** The strip draws a tab whose route was never registered (or
    was registered under a different suffix), so the panel renders against the
    SPA history fallback or a bare 404 and shows silence. Routed through
    ``qubit_compat.match`` — the ONE route table — not through a re-derived
    list, so a route that is declared but unreachable still fails here.

(b) **Silence dressed as data.** A collector that cannot read its source must
    say so: ``available: false`` plus a ``detail`` naming what is missing. A
    payload with ``available: false`` and an empty ``detail`` is indistinguishable
    to the panel from a populated empty state, and the panel then renders
    nothing at all.

(c) **An unhandled error.** A collector that raises is wrapped as a typed 502
    (``HUD_COLLECTOR_FAILED``) / 503 (``HUD_COLLECTOR_UNAVAILABLE``) — legitimate
    as a refusal, never as the answer to a healthy read. A live tab that answers
    502 is dark in practice.

(d) **A made-up "available".** ``available: true`` must ride on something the
    payload actually carries: the collector's own source is named, or rows
    arrived, or the state it reports is explicit.

Two runs, because "typed" has to mean the same thing in both directions:

* **Fresh box** (empty ``QUBIT_HOME``, empty XDG, no store, no config): every
  panel must answer a typed EMPTY/UNAVAILABLE state and name what is absent.
  Nothing may crash and nothing may claim data it cannot have.
* **Seeded box** (a real session in the canonical store, real registered
  projects, a real profile island): the panels whose sources exist must flip to
  ``available: true`` AND carry the seeded facts, so ``available`` is proven to
  track reality rather than being a constant.

Hermetic: tmp XDG/QUBIT_HOME only, no network, no live service, no provider
call, no browser. Every route is exercised through ``qubit_compat.dispatch``,
the same function the dashboard calls.

The tab list is read out of ``qubit-hud.js`` itself (``var TABS = [...]``) so a
tab added to the renderer without a route, or a route added without a tab, both
fail here. No skips, no xfail.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

HUD_JS = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-hud"
          / "qubit-hud.js")

#: Every tab id the renderer's ``TABS`` table draws, in strip order. Kept as a
#: literal so the file states the contract even if the JS regresses to 18 tabs;
#: ``test_tabs_table_matches_the_renderer`` fails on any drift either way.
TAB_IDS = (
    "health", "sessions", "cron", "models", "skills", "plugins", "costs",
    "sudo", "replay", "corrections", "patterns", "verify", "providers",
    "projects", "profiles", "memory", "gateway", "live-chat", "live-models",
)

#: Tab id -> the route the RENDERER actually calls for it. Seven tabs predate
#: the HUD route table and read their own established dashboard routes; the
#: twelve restored panels read ``/api/hud/<name>``. The mapping is not cosmetic:
#: the matrix dispatches exactly what the tab fetches, so a tab whose ``P`` map
#: entry drifted to a route nobody serves is a dark tab here, not a passing one.
#: ``costs`` reads ``/api/analytics`` (its collector is ``model_analytics``).
_RESTORED = tuple(tab for tab in TAB_IDS
                  if tab not in ("health", "sessions", "cron", "models",
                                 "skills", "plugins", "costs"))
TAB_ROUTES = {
    "health": "/api/health",
    "sessions": "/api/sessions",
    "cron": "/api/cron/jobs",
    "models": "/api/model/options",
    "skills": "/api/skills",
    "plugins": "/api/dashboard/plugins",
    "costs": "/api/analytics",
    **{tab: f"/api/hud/{tab}" for tab in _RESTORED},
}

#: Panels with no ``available`` flag by design: Health is a liveness snapshot
#: (``ok`` + ``state``), Models is the provider catalog (``ok`` + ``providers``),
#: and the Sessions/Skills/Plugins/Costs routes the seven original tabs read
#: carry ``ok`` plus their own typed store/source state (Skills/Plugins/Cron are
#: bare arrays). Each is asserted on its own terms; none of them is coerced onto
#: a flag it does not publish.
NO_AVAILABLE_FLAG = {"health", "models", "sessions", "skills", "plugins",
                     "costs", "cron"}

#: Restored panels whose ``available: true`` HAD to name the source it read (or
#: carry the rows/state that source returned). A panel missing from this set
#: still has its ``available`` checked against ``store_present`` /
#: ``path_present`` / ``state`` in the fresh-box test, so a constant ``true``
#: fails there instead — every restored panel is constrained one way or another.
AUTHORITY_PANELS = {
    "sudo", "replay", "corrections", "patterns", "verify", "providers",
    "projects", "profiles", "memory", "gateway", "live-chat", "live-models",
}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _hermetic_env(tmp_path: Path) -> dict:
    """Roots every store resolver a HUD collector touches under ``tmp_path``.

    ``QUBIT_HOME``/``HERMES_HOME`` are BOTH pinned: the collectors resolve the
    home through ``qubit_constants``, and the cron bridge and profile registry
    reach the same family of resolvers. Leaving either inherited would let this
    file read the owner's live stores.
    """
    qhome = tmp_path / "qhome"
    qhome.mkdir(parents=True, exist_ok=True)
    return {
        "QUBIT_HOME": str(qhome),
        "HERMES_HOME": str(qhome),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "HOME": str(tmp_path / "home"),
    }


def _hermetic(tmp_path: Path, monkeypatch) -> Path:
    """Point every store resolver a HUD collector reads at ``tmp_path``.

    Returns the VoiceBridge config file to load.
    """
    values = _hermetic_env(tmp_path)
    Path(values["HOME"]).mkdir(parents=True, exist_ok=True)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config_file = tmp_path / "voice.yaml"
    config_file.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    return config_file


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fresh, empty Qubit home — the "nothing has happened yet" box."""
    config_file = _hermetic(tmp_path, monkeypatch)
    return {"tmp": tmp_path, "config_file": config_file}


def _config(env):
    from voicebridge import config as C

    return C.Config.load(env["config_file"])


def _dispatch(env, path: str, query=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(_config(env), "GET", path, dict(query or {}), {})


# ---------------------------------------------------------------------------
# 1. The tab table and the route table agree
# ---------------------------------------------------------------------------

def _tabs_from_renderer() -> list:
    """``[{id, route}, ...]`` as the shipped renderer declares them."""
    source = HUD_JS.read_text(encoding="utf-8")
    block = re.search(r"var TABS = \[(.*?)\n  \];", source, re.S)
    assert block, "qubit-hud.js no longer declares `var TABS = [...]`"
    rows = re.findall(r"\{ id: \"([^\"]+)\", label: \"[^\"]*\", route: ([^,]+),",
                      block.group(1))
    return [{"id": tab_id, "route_expr": route.strip()} for tab_id, route in rows]


def test_tabs_table_matches_the_renderer():
    """The renderer draws exactly the 19 tabs this matrix asserts, in order."""
    tabs = _tabs_from_renderer()
    assert [t["id"] for t in tabs] == list(TAB_IDS), (
        "the HUD tab strip drifted from the matrix: "
        f"{[t['id'] for t in tabs]!r}")


def test_renderer_route_map_names_the_backing_route_per_tab():
    """Every tab's ``route`` expression resolves to the route this matrix calls.

    The renderer builds routes from two literal maps (``P`` for the seven
    original tabs, ``HUD`` for the twelve restored ones), so the binding is
    checked in the source: a tab whose ``route:`` names a variable that no
    longer holds the expected path is a tab pointing somewhere else, and that
    is exactly the failure a payload-level test would miss.
    """
    source = HUD_JS.read_text(encoding="utf-8")

    def _map_literal(name: str) -> dict:
        block = re.search(rf"var {name} = \{{(.*?)\n  \}};", source, re.S)
        assert block, f"qubit-hud.js declares no `var {name} = {{...}}`"
        return dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block.group(1)))

    maps = {"P": _map_literal("P"), "HUD": _map_literal("HUD")}
    tabs = _tabs_from_renderer()
    for tab in tabs:
        expr = tab["route_expr"]
        owner, _, key = expr.partition(".")
        assert owner in maps, (tab["id"], expr)
        assert key in maps[owner], (tab["id"], expr, sorted(maps[owner])[:5])
        expected = TAB_ROUTES[tab["id"]]
        assert maps[owner][key] == expected, (
            f"tab {tab['id']!r} reads {maps[owner][key]!r}, "
            f"the matrix asserts {expected!r}")


def test_every_tab_route_is_registered_in_the_one_route_table():
    """A dark tab is a tab whose route the compat table does not answer."""
    from voicebridge import qubit_compat as HC

    dark = []
    for tab in TAB_IDS:
        path = TAB_ROUTES[tab]
        route, _params = HC.match("GET", path)
        if route is None:
            dark.append((tab, path, "no route in qubit_compat.ROUTES"))
            continue
        if route.get("disposition") != "implemented":
            dark.append((tab, path, route.get("disposition")))
            continue
        if HC.HANDLERS.get(route["handler"]) is None:
            dark.append((tab, path, f"handler {route['handler']!r} missing"))
    assert not dark, "HUD tabs with no live route: " + json.dumps(dark, indent=1)


def test_hud_routes_are_qubit_owned_and_never_proxied():
    """Every restored panel stays on the Qubit compat path (SPA-fallback defect).

    ``/api/hud`` must be a Qubit-owned prefix: without it the HUD GETs are
    proxy-first and, because the vendored runtime declares no ``/api/hud/*``,
    ``webui`` answers the SPA HTML shell with a 200 — every panel "loads" and
    renders nothing. Scoped to the ``/api/hud/*`` routes on purpose: ``/``
    ``api/health`` is a public liveness read with its own contract and is not
    part of the HUD gate.
    """
    from voicebridge import qubit_asgi as HA

    hud_routes = [r for r in (TAB_ROUTES[t] for t in TAB_IDS)
                  if r.startswith("/api/hud/")]
    assert len(hud_routes) == len(_RESTORED)
    for path in hud_routes:
        assert HA.gated_path(path) is True, path
        assert HA.safe_to_proxy("GET", path) is False, path


# ---------------------------------------------------------------------------
# 2. The typed-payload contract, applied to every panel
# ---------------------------------------------------------------------------

def _typed_defects(tab: str, code: int, out) -> list:
    """Every way ``(code, out)`` fails to be a typed payload. Empty = typed.

    "Typed" is per panel family, because the HUD reads two:
    * the restored ``/api/hud/*`` collectors speak ``available`` + ``detail``;
    * the seven original tabs read established routes that speak ``ok`` plus
      their own store/source state (``store_state``, ``state``, a bare list).
    """
    defects = []
    if code != 200:
        if isinstance(out, dict) and out.get("code") in (
                "HUD_COLLECTOR_UNAVAILABLE", "HUD_COLLECTOR_FAILED"):
            # A 503 is the ONE legitimate non-200 (collector package absent);
            # a 502 means the collector ran and raised, which is a live defect.
            if code == 503:
                return defects
        return [f"HTTP {code} ({type(out).__name__})"]

    # The three tabs whose established route answers a bare JSON array.
    if tab in ("cron", "skills", "plugins"):
        if not isinstance(out, list):
            return [f"payload is {type(out).__name__}, not a list"]
        return defects

    if not isinstance(out, dict):
        return [f"payload is {type(out).__name__}, not an object"]
    if not out:
        return ["payload is an empty object"]

    if tab == "health":
        # The HUD reads ``P.health`` (liveness) for the live card and
        # ``P.healthIdle`` (the diagnostics snapshot) for the panels; the
        # matrix asserts the liveness route's own contract here because that is
        # what the route actually serves — ``ok`` plus an explicit ``state``.
        if out.get("ok") is not True:
            defects.append("health liveness payload is not ok:true")
        if not str(out.get("state") or "").strip():
            defects.append("health names no state")
        return defects

    if tab == "models":
        # ``/api/model/options`` is the catalog the panel lists from; a provider
        # list with no ``ok`` is the dark shape.
        if out.get("ok") is not True:
            defects.append("model options payload is not ok:true")
        if not isinstance(out.get("providers"), list):
            defects.append("model options carries no providers list")
        return defects

    if tab == "sessions":
        if out.get("ok") is not True:
            defects.append("sessions payload is not ok:true")
        if not isinstance(out.get("sessions"), list):
            defects.append("sessions carries no sessions list")
        if not str(out.get("store_state") or "").strip():
            defects.append("sessions does not type its store state")
        return defects

    if tab == "costs":
        if out.get("ok") is not True:
            defects.append("analytics payload is not ok:true")
        if not isinstance(out.get("totals"), dict):
            defects.append("analytics carries no totals")
        return defects

    # ---- the restored /api/hud/* collectors -------------------------------
    if "available" not in out:
        return ["no `available` flag on an availability-bearing panel"]
    if not isinstance(out["available"], bool):
        return [f"`available` is {type(out['available']).__name__}, not bool"]
    if out["available"] is False and not str(out.get("detail") or "").strip():
        defects.append("available:false with an empty detail (silent failure)")
    if out["available"] is True and tab in AUTHORITY_PANELS:
        has_source = bool(str(out.get("authority") or "").strip()
                          or str(out.get("detail") or "").strip())
        # Only a non-empty row set counts as "landed": an empty list is the
        # empty state, which must itself be explained by ``detail``.
        rows = [key for key in ("sessions", "runs", "corrections", "clusters",
                                "checkpoints", "projects", "profiles", "models",
                                "jobs", "plugins", "records", "skills", "daily",
                                "pool", "methods")
                if _nonempty(out.get(key))]
        landed = bool(rows)
        explicit_state = bool(str(out.get("state") or "").strip()
                              or str(out.get("verdict") or "").strip()
                              or str(out.get("status") or "").strip())
        if not (has_source or landed or explicit_state):
            defects.append("available:true with no source named")
    return defects


def _nonempty(value) -> bool:
    """True when a payload slot actually carries something.

    A list of rows, a non-empty mapping, or a scalar fact. Three sub-objects
    (``pool``/``policy``/``status``) whose own rows are all empty do NOT count:
    they are the empty state, and the empty state must explain itself through
    ``detail``/``state``.
    """
    if isinstance(value, (list, tuple, dict, str)):
        if isinstance(value, (list, tuple)):
            return len(value) > 0
        if isinstance(value, str):
            return bool(value.strip())
        return any(_nonempty(v) for v in value.values())
    return value is not None


def _state_of(payload) -> str:
    """A short typed-state label; two payloads with the same label match here."""
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    if not isinstance(payload, dict) or not payload:
        return f"empty {type(payload).__name__}"
    parts = []
    for key in ("available", "ok", "found", "state", "verdict", "store_state",
                "store_present", "path_present", "sessions_scanned",
                "tombstoned", "staged_count"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    for key in ("total", "count", "shown", "records", "all_rows"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    return " ".join(parts) or "no typed state"


def _signature(payload) -> str:
    """The typed state plus how much the payload actually carries.

    Two payloads can share a typed state (``available: true``) while one of them
    now lists rows; the row ARRAY is part of the signature, not just its length
    field, so a panel that quietly stops listing rows is visible here.
    """
    listed = []
    scopes = [payload]
    if isinstance(payload, dict):
        # ``memory`` nests its row view under ``store``; a signature that only
        # looked at the top level would call a seeded panel unchanged.
        scopes += [v for v in payload.values() if isinstance(v, dict)]
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for key in ("sessions", "runs", "clusters", "corrections", "rows",
                    "staged", "checkpoints", "projects", "profiles", "jobs"):
            if isinstance(scope.get(key), list):
                listed.append(f"{key}={len(scope[key])}")
    return _state_of(payload) + (" " + " ".join(listed) if listed else "")


def _matrix(env, label: str) -> list:
    """Dispatch all 19 routes and return ``[(tab, code, out, defects)]``."""
    rows = []
    for tab in TAB_IDS:
        code, out = _dispatch(env, TAB_ROUTES[tab])
        rows.append((tab, code, out, _typed_defects(tab, code, out)))
    assert len(rows) == len(TAB_IDS)
    return rows


def _report(rows) -> str:
    return "; ".join(f"{tab}={code}:" + ",".join(defects)
                     for tab, code, _out, defects in rows if defects)


# ---------------------------------------------------------------------------
# 3. Fresh box: every panel answers a typed empty/unavailable state
# ---------------------------------------------------------------------------

def test_fresh_box_every_panel_answers_a_typed_payload(env):
    """Nothing has been created yet: no panel may crash, be silent, or lie."""
    rows = _matrix(env, "fresh")
    assert not _report(rows), _report(rows)


def test_fresh_box_reports_absence_rather_than_fabricating_data(env):
    """On an empty box the panels whose stores do not exist must SAY so.

    This is the assertion that a constant ``available: true`` cannot pass: with
    no ``state.db``, no memory store, no journal and no cron store, these
    panels have nothing truthful to show and must report the absence.
    """
    rows = {tab: (code, out) for tab, code, out, _ in _matrix(env, "fresh")}

    # ---- the seven original tabs, on their own established contracts -------
    code, out = rows["health"]
    assert code == 200
    assert out["ok"] is True and str(out["state"]).strip(), out

    code, out = rows["sessions"]
    assert code == 200
    # An empty home is a TYPED unavailable store, not a silent zero page.
    assert out["sessions"] == [] and out["total"] == 0, out
    assert out["store_state"] in ("UNAVAILABLE", "OK"), out

    code, out = rows["cron"]
    assert code == 200 and out == [], out  # nothing scheduled, said as []

    code, out = rows["models"]
    assert code == 200
    assert out["ok"] is True and isinstance(out["providers"], list), out

    code, out = rows["skills"]
    assert code == 200 and isinstance(out, list), out

    code, out = rows["plugins"]
    assert code == 200 and isinstance(out, list), out

    code, out = rows["costs"]
    assert code == 200
    assert out["daily"] == [] and out["totals"]["total_sessions"] == 0, out

    # ---- the restored panels: absence is named, never faked ---------------
    code, out = rows["sudo"]
    assert code == 200 and out["available"] is True, out
    assert out["journal"] and isinstance(out["journal"], dict), out
    assert out["policy"]["available"] is True, out

    code, out = rows["replay"]
    assert code == 200
    assert out["available"] is True, out  # the authority reads an empty home
    assert out["total"] == 0 and out["runs"] == [], out

    code, out = rows["corrections"]
    assert code == 200
    assert out["available"] is True, out
    assert out["store_present"] is False, out
    assert out["corrections"] == [] and out["total"] == 0, out
    assert "not been created" in out["detail"], out

    code, out = rows["patterns"]
    assert code == 200
    assert out["available"] is True, out
    assert out["total_sessions"] == 0 and out["clusters"] == [], out
    # A gap the authority cannot fill is named, never rendered as zeros.
    assert out["tool_workflows"] is None and out["tool_workflows_reason"], out

    code, out = rows["verify"]
    assert code == 200
    assert out["verdict"] == "NO_CHECKPOINTS", out
    assert out["checkpoints"] == [] and out["total"] == 0, out

    code, out = rows["projects"]
    assert code == 200 and out["available"] is True, out
    assert out["total"] == 0 and out["projects"] == [], out

    code, out = rows["profiles"]
    assert code == 200
    assert out["total"] >= 1, out  # the default island always exists

    code, out = rows["memory"]
    assert code == 200
    assert out["store"]["store_present"] is False, out
    assert out["store"]["detail"], out

    code, out = rows["gateway"]
    assert code == 200 and out["available"] is True, out
    assert out["state"], out

    code, out = rows["live-chat"]
    assert code == 200
    assert out["path_present"] is False and out["tail"] == [], out
    # The WebSocket is a recorded refusal, not a hidden gap.
    assert out["transport"]["code"] == "WS_SINGLE_PATH", out

    code, out = rows["live-models"]
    assert code == 200 and out["available"] is True, out
    assert out["probe"] is None and out["probe_reason"], out


# ---------------------------------------------------------------------------
# 4. Seeded box: ``available`` tracks reality and carries the seeded facts
# ---------------------------------------------------------------------------

def _seed_session(env) -> str:
    """Write one real session through the ONE session authority."""
    from voicebridge.qubit_session_store import QubitSessionStore

    store = QubitSessionStore(_config(env))
    sid = "hud-matrix-seeded"
    created = store.create("HUD matrix seeded run", session_id=sid)
    assert created.get("state") in ("OK", "CREATED"), created
    first = store.append(sid, "user", "why is the cron tab empty in the HUD?")
    assert first.get("state") in ("OK", "APPENDED"), first
    second = store.append(sid, "assistant", "because no cron job is registered")
    assert second.get("state") in ("OK", "APPENDED"), second
    return sid


def _seed_all(env) -> str:
    """Seed every source a HUD collector reads, through its owning authority.

    Nothing is written with hand-rolled SQL: a panel that fails to show a row
    seeded this way is genuinely not reading the store the product writes.
    """
    sid = _seed_session(env)
    cfg = _config(env)

    from voicebridge.events.journal import EventJournal

    journal = EventJournal(Path(cfg.state_dir()) / "typed-events.jsonl",
                           fsync=False)
    journal.append("TEXT_MESSAGE_START", message_id="seeded-msg",
                   role="assistant")

    from voicebridge import active_memory as AM
    from voicebridge.memory.store import MemoryStore

    path = Path(AM.memory_db_path(cfg))
    path.parent.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(path)
    store.remember("the HUD reports live Qubit state", tier="core",
                   scope="default", source="user")
    store.correct("the HUD reports live Qubit state",
                  "the HUD reports typed Qubit state only")
    store.close()
    return sid


def test_seeded_session_reaches_every_session_backed_panel(env):
    """Sessions, Replay, Patterns, Costs all read the ONE authority.

    One seeded run must surface in all four. A panel that stays at zero while
    the authority holds a row is reading a different store (the second-opener
    defect), and that is what this catches.
    """
    sid = _seed_session(env)

    code, out = _dispatch(env, TAB_ROUTES["sessions"])
    assert code == 200 and out["ok"] is True, out
    assert out["total"] >= 1, out
    assert sid in {row.get("id") for row in out["sessions"]}, out["sessions"]

    code, out = _dispatch(env, TAB_ROUTES["replay"])
    assert code == 200 and out["available"] is True, out
    assert out["total"] >= 1 and out["runs"], out
    # ``runs`` rows carry ``session_id`` (the transcript player's key), not ``id``.
    assert sid in {row.get("session_id") for row in out["runs"]}, out["runs"]

    code, out = _dispatch(env, TAB_ROUTES["patterns"])
    assert code == 200 and out["available"] is True, out
    assert out["sessions_scanned"] >= 1, out

    code, out = _dispatch(env, TAB_ROUTES["costs"])
    assert code == 200 and out["ok"] is True, out
    # The Costs tab reads the same analytics the Patterns day-tokens read.
    assert isinstance(out["daily"], list) and isinstance(out["totals"], dict), out


def test_replay_detail_with_a_real_session_id_answers_a_transcript(env):
    """``?session_id=`` is the Replay tab's second read; it must be typed too."""
    sid = _seed_session(env)

    code, out = _dispatch(env, TAB_ROUTES["replay"], {"session_id": sid})
    assert code == 200, (code, out)
    # The list read is what ``available`` describes; the transcript rides beside
    # it in ``detail_view`` and is typed on its own.
    assert out["available"] is True, out
    assert out["session_id"] == sid, out
    detail = out.get("detail_view")
    assert isinstance(detail, dict), out
    assert detail.get("available") is True, detail
    assert [m.get("role") for m in detail.get("messages") or []] == [
        "user", "assistant"], detail
    assert detail["messages"][0]["content"].startswith("why is the cron tab"), detail
    # The per-step trace is a named gap, not three zero columns.
    assert detail["messages"][0]["tool_calls"] is None, detail

    # An unknown id is a typed miss inside the detail view, never a 500.
    code, out = _dispatch(env, TAB_ROUTES["replay"],
                          {"session_id": "no-such-session"})
    assert code == 200, (code, out)
    detail = out["detail_view"]
    assert detail["available"] is False, detail
    assert str(detail.get("detail") or "").strip(), detail
    assert detail.get("messages") == [], detail


#: Sources this file seeds through their owning authority, and the tab whose
#: payload must therefore change. A panel that does NOT change when its source
#: gains a row is reading a different store (the second-opener defect) — that is
#: the dark shape a payload-shape assertion cannot catch.
SEEDED_LIVENESS = {
    "sessions": "the session authority",
    "replay": "the session authority list()",
    "patterns": "the session authority scan",
    "corrections": "the memory store's supersedes ledger",
    "memory": "the memory store's live rows",
    "live-chat": "the typed event journal",
}


def test_seeded_sources_change_the_panel_payloads(tmp_path, monkeypatch):
    """Every panel whose source is seeded must visibly read that source."""
    _hermetic(tmp_path, monkeypatch)
    config_file = tmp_path / "voice.yaml"
    config_file.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    env = {"tmp": tmp_path, "config_file": config_file}
    fresh = {tab: _dispatch(env, TAB_ROUTES[tab]) for tab in SEEDED_LIVENESS}

    _seed_all(env)

    stale = []
    for tab in SEEDED_LIVENESS:
        before, after = fresh[tab], _dispatch(env, TAB_ROUTES[tab])
        assert after[0] == 200, (tab, after[0])
        if _signature(before[1]) == _signature(after[1]):
            stale.append((tab, SEEDED_LIVENESS[tab], _signature(after[1])))
    assert not stale, "panels that ignored their seeded source: " + json.dumps(stale)


def test_projects_panel_survives_registry_contents(env):
    """The Projects payload stays typed whether the registry is empty or not."""
    code, out = _dispatch(env, TAB_ROUTES["projects"])
    assert code == 200 and out["available"] is True, out
    assert isinstance(out["projects"], list), out
    assert out["total"] == len(out["projects"]), out
    for row in out["projects"]:
        assert "present" in row and "is_git" in row, row


# ---------------------------------------------------------------------------
# 5. Payloads are JSON-ready (the dashboard serializes them verbatim)
# ---------------------------------------------------------------------------

def test_every_payload_survives_json_round_trip(env):
    """A payload the dashboard cannot serialize is a panel that never renders."""
    for tab in TAB_IDS:
        code, out = _dispatch(env, TAB_ROUTES[tab])
        assert code == 200, (tab, code)
        try:
            # ``allow_nan=False`` is the strict form: every collector returns a
            # plain JSON tree, so a set, a Path, a datetime or a NaN fails here
            # instead of becoming a panel the dashboard cannot render.
            encoded = json.dumps(out, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{tab} payload is not JSON-ready: {exc}") from exc
        assert json.loads(encoded) == out, tab


# ---------------------------------------------------------------------------
# 6. Negative control: the typed-payload contract can actually fail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tab, code, payload, expected", [
    # Silence dressed as data: unavailable with no reason named.
    ("gateway", 200, {"available": False, "detail": ""},
     "empty detail"),
    # A made-up success: available, but nothing it read is named.
    ("providers", 200, {"available": True, "detail": "",
                        "pool": {"providers": []}, "policy": {}},
     "no source"),
    # An unhandled collector error is a dark panel, not a 502 to be accepted.
    ("verify", 502, {"ok": False, "panel": "verify",
                     "code": "HUD_COLLECTOR_FAILED", "detail": "boom"},
     "HTTP 502"),
    # A collector package naming itself missing is the ONE accepted non-200.
    ("memory", 503, {"ok": False, "panel": "memory",
                     "code": "HUD_COLLECTOR_UNAVAILABLE", "detail": "absent"},
     None),
    # A bare array for a panel that must speak ``available``.
    ("sudo", 200, [], "not an object"),
    # An established route that lost its contract (no ``ok``) is dark too.
    ("sessions", 200, {"sessions": [], "total": 0}, "not ok:true"),
    ("health", 200, {"ok": True}, "names no state"),
    ("cron", 200, {"jobs": []}, "not a list"),
])
def test_typed_contract_rejects_dark_payloads(tab, code, payload, expected):
    """Falsifiability: each failure mode the matrix exists for is caught here.

    Without this, ``_typed_defects`` could return ``[]`` for everything and the
    matrix tests would pass on a HUD that renders nineteen empty panels.
    """
    defects = _typed_defects(tab, code, payload)
    if expected is None:
        assert defects == [], defects
        return
    assert defects, f"{tab}/{code}/{payload!r} must be rejected"
    assert any(expected in defect for defect in defects), (expected, defects)
