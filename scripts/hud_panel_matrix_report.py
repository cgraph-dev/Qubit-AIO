"""Report which of the 19 HUD panels are genuinely wired and which are dark.

Reads the panel matrix test's own tables, then dispatches every route twice:
once against an EMPTY Qubit home ("fresh") and once against a home seeded with
real rows for every source a collector can read ("seeded"). A panel is

  WIRED   the route answered a typed 200 in both runs, and its payload left the
          empty state once the source it reads actually held rows;
  EMPTY   the route answered a typed 200 in both runs, but stayed in its typed
          empty/unavailable state even with rows present — honest, but the
          panel has nothing to show from these seeds;
  DARK    the route 404'd, answered an unhandled 502, returned a non-typed
          payload, or dropped out of the tab table.

This script only REPORTS; `tests/test_hud_panel_matrix_w18.py` is the gate.
Heremtic: all state under a tmp dir, never the owner's live stores.

Usage:
    .venv/bin/python scripts/hud_panel_matrix_report.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: The seeding is deliberately small: one row per source is enough to prove a
#: panel is not permanently stuck in its empty state. Sources this script cannot
#: write through a public API (a checkpoint journal, a cron job store, the
#: credential pool, the project registry) are declared NOT SEEDED and their
#: panels are reported as typed-but-unseeded rather than guessed at.
SEEDS_NOTE = (
    "seeded through the owning authority: 1 session w/ 2 messages · "
    "1 typed event · 1 memory row superseded by 1 correction"
)
UNSEEDED = {
    "cron": "no public cron-store writer here; asserted by the fresh-box matrix",
    "verify": "checkpoint creation is an operator command, not seeded here",
    "providers": "credential entries are operator-owned secrets, never seeded",
    "projects": "project registration is a user action, not seeded here",
    "models": "provider catalog comes from the live models.dev cache",
    "skills": "skill discovery scans the bundled + profile tiers",
    "plugins": "plugin discovery scans the install, not a test store",
}


def _hermetic(root: Path) -> None:
    qhome = root / "qhome"
    qhome.mkdir(parents=True, exist_ok=True)
    (root / "home").mkdir(parents=True, exist_ok=True)
    for key, value in {
        "QUBIT_HOME": str(qhome),
        "HERMES_HOME": str(qhome),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "HOME": str(root / "home"),
    }.items():
        os.environ[key] = value


def _config(root: Path):
    from voicebridge import config as C

    cfg_file = root / "voice.yaml"
    if not cfg_file.is_file():
        cfg_file.write_text("logging:\n  jsonl: false\n", encoding="utf-8")
    return C.Config.load(cfg_file)


def _dispatch(cfg, path: str):
    """The dashboard's own dispatch, minus the audit write.

    ``qubit_compat.dispatch`` appends a ``served`` row to the audit log on
    every call; nineteen panels x two runs against a tmp state dir is fine, but
    a report script has no business writing audit rows for reads it invented,
    so the route is matched and its handler called directly — the same handler
    the dispatcher would have called.
    """
    from voicebridge import qubit_compat as HC

    route, params = HC.match("GET", path.split("?")[0].rstrip("/") or "/")
    if route is None:
        return 404, {"ok": False, "code": "NOT_FOUND", "detail": path}
    if route.get("disposition") != "implemented":
        return 403, {"ok": False, "code": route.get("disposition")}
    handler = HC.HANDLERS.get(route["handler"])
    if handler is None:
        return 500, {"ok": False, "code": "NO_HANDLER",
                     "detail": route["handler"]}
    return handler(cfg, {}, {}, params)


def _seed(root: Path, cfg) -> None:
    """Put one real row behind every source a HUD collector reads.

    Each seed goes through the OWNING authority (never a hand-written SQLite
    insert), so a panel that fails to show the row is genuinely not reading the
    store the rest of the product writes. A seed that cannot be built is
    reported — it is never silently skipped, because a silent skip would turn
    a real gap into a false "EMPTY".
    """
    def note(what: str, exc: Exception) -> None:
        print(f"  ! {what} seed skipped: {type(exc).__name__}: {exc}")

    from voicebridge.qubit_session_store import QubitSessionStore

    try:
        store = QubitSessionStore(cfg)
        sid = "hud-report-seeded"
        store.create("HUD report seeded run", session_id=sid)
        store.append(sid, "user", "why is the cron tab empty in the HUD?")
        store.append(sid, "assistant", "because no cron job is registered")
    except Exception as exc:  # noqa: BLE001
        note("session", exc)

    try:
        from voicebridge.events.journal import EventJournal

        journal = EventJournal(Path(cfg.state_dir()) / "typed-events.jsonl",
                               fsync=False)
        journal.append("TEXT_MESSAGE_START", message_id="seeded-msg",
                       role="assistant")
    except Exception as exc:  # noqa: BLE001
        note("typed event", exc)

    try:
        from voicebridge import active_memory as AM
        from voicebridge.memory.store import MemoryStore

        path = Path(AM.memory_db_path(cfg))
        path.parent.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(path)
        store.remember("the HUD reports live Qubit state",
                       tier="core", scope="default", source="user")
        store.correct("the HUD reports live Qubit state",
                      "the HUD reports typed Qubit state only")
        store.close()
    except Exception as exc:  # noqa: BLE001
        note("memory", exc)


def _state_of(payload) -> str:  # noqa: D401 - short label, not prose
    """A short, honest state label for one payload."""
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    if not isinstance(payload, dict) or not payload:
        return f"empty {type(payload).__name__}"
    parts = []
    for key in ("available", "ok", "found", "state", "verdict", "store_state",
                "store_present", "path_present"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    for key in ("total", "count", "shown", "records"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    return " ".join(parts) or "no typed state"


def _import_matrix():
    """Load the matrix test module without importing it as a test."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "hud_panel_matrix", REPO / "tests" / "test_hud_panel_matrix_w18.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load tests/test_hud_panel_matrix_w18.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args(argv)

    matrix = _import_matrix()

    with tempfile.TemporaryDirectory(prefix="hud-report-") as tmp:
        root = Path(tmp)
        _hermetic(root)
        cfg = _config(root)
        fresh = {tab: _dispatch(cfg, matrix.TAB_ROUTES[tab])
                 for tab in matrix.TAB_IDS}
        _seed(root, cfg)
        seeded = {tab: _dispatch(cfg, matrix.TAB_ROUTES[tab])
                  for tab in matrix.TAB_IDS}

    rows = []
    for tab in matrix.TAB_IDS:
        route = matrix.TAB_ROUTES[tab]
        f_code, f_out = fresh[tab]
        s_code, s_out = seeded[tab]
        defects = (matrix._typed_defects(tab, f_code, f_out)
                   + matrix._typed_defects(tab, s_code, s_out))
        depth = len(json.dumps(s_out, default=str))
        if (f_code, s_code) != (200, 200) or defects:
            verdict = "DARK"
        elif _state_of(f_out) != _state_of(s_out):
            verdict = "WIRED"
        elif tab in UNSEEDED:
            verdict = "NOT-SEEDED"
        elif depth > len(json.dumps(f_out, default=str)):
            # The payload grew (a row count, a tier breakdown) without its
            # headline state changing: the panel read real data.
            verdict = "WIRED"
        else:
            verdict = "EMPTY"
        rows.append({
            "tab": tab,
            "route": route,
            "verdict": verdict,
            "fresh": f"{f_code} {_state_of(f_out)}",
            "seeded": f"{s_code} {_state_of(s_out)}",
            "defects": defects,
        })

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    width = max(len(r["tab"]) for r in rows)
    print(f"seeds — {SEEDS_NOTE}\n")
    print(f"{'panel'.ljust(width)}  {'verdict':6}  {'fresh':38}  seeded")
    print("-" * (width + 60))
    for row in rows:
        print(f"{row['tab'].ljust(width)}  {row['verdict']:6}  "
              f"{row['fresh']:38}  {row['seeded']}")
        for defect in row["defects"]:
            print(f"{'':{width}}          ! {defect}")
    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in ("WIRED", "EMPTY", "NOT-SEEDED", "DARK")}
    print(f"\n{counts['WIRED']} wired · {counts['EMPTY']} typed-but-empty · "
          f"{counts['NOT-SEEDED']} typed, source not seeded here · "
          f"{counts['DARK']} dark")
    return 1 if counts["DARK"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
