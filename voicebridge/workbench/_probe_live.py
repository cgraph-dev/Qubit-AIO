"""Live call probe for the workbench seam — run inside the worktree.

Prints real numbers from the live home (no fixture, no mock):
  * how many sessions the canonical store reports, and one session id
  * one real filename from the workspace tree
  * the shape of each adapter's refusal when handed bad input

Run:
  PYTHONPATH=$QUBIT_REPO/qubit_runtime \
    $QUBIT_REPO/.venv/bin/python \
    voicebridge/workbench/_probe_live.py
"""
from __future__ import annotations

import json
import sys

from voicebridge.workbench import adapters_sessions as S
from voicebridge.workbench import adapters_workspace as WS


def _show(label: str, out: dict, keys: tuple) -> None:
    print(f"\n--- {label} ---")
    for k in keys:
        v = out.get(k)
        if isinstance(v, (list, dict)) and not isinstance(v, str):
            print(f"  {k}: <{type(v).__name__} len={len(v)}>")
        else:
            text = str(v)
            print(f"  {k}: {text[:160]}")


def main() -> int:
    cfg = None
    try:
        from voicebridge.config import Config  # type: ignore

        cfg = Config.load()
    except Exception as exc:
        print(f"  NOTE: Config.load failed ({exc}); passing cfg=None")
    rc = 0

    page = S.list_sessions(cfg, limit=25)
    _show("list_sessions", page, ("state", "code", "total", "shown",
                                  "has_more", "sessions"))
    total = int(page.get("total") or 0)
    print(f"  REAL: total={total} shown={page.get('shown')}")
    if page.get("state") != "OK":
        print(f"  REFUSED: {page.get('code')} — {page.get('detail')}")
        rc = 1
    sid = ""
    if page.get("sessions"):
        first = page["sessions"][0]
        sid = str(first.get("id") or "")
        print(f"  first session: id={sid!r} messages={first.get('message_count')}")

    if sid:
        tr = S.read_transcript(cfg, sid, limit=5)
        _show("read_transcript(first)", tr,
              ("state", "code", "total", "shown", "messages"))
        tl = S.read_timeline(cfg, sid, limit=10)
        _show("read_timeline(first)", tl,
              ("state", "code", "turns", "events", "events_state"))

    bad = S.read_transcript(cfg, "definitely-not-a-session-xyz")
    _show("read_transcript(missing id)", bad, ("state", "code", "detail"))
    empty = S.send_turn(cfg, sid, "")
    _show("send_turn(empty text)", empty, ("state", "code", "detail"))

    tree = WS.list_tree(cfg)
    _show("list_tree(default)", tree,
          ("state", "code", "workspace", "path", "shown", "truncated",
           "entries"))
    if tree.get("state") != "OK":
        print(f"  DEFAULT FOLDER NOT USABLE: {tree.get('code')} — "
              f"{str(tree.get('detail'))[:120]}")
        # Fall back to the first REGISTERED project folder so the tree path is
        # still proven against real files rather than only against refusals.
        roots = tree.get("roots") or []
        if roots:
            tree = WS.list_tree(cfg, roots[0])
            _show("list_tree(first registered root)", tree,
                  ("state", "code", "workspace", "shown", "truncated",
                   "entries"))
    if tree.get("entries"):
        names = [e.get("name") for e in tree["entries"][:5]]
        print(f"  REAL: first entries = {names}")
        files = [e for e in tree["entries"] if e.get("type") == "file"]
        if files:
            pick = files[0]
            print(f"  REAL: one filename from the tree = {pick.get('name')!r}"
                  f" ({pick.get('size')} bytes)")
            pv = WS.preview_file(cfg, pick.get("path") or "",
                                 workspace=tree.get("workspace") or "")
            _show("preview_file(first file)", pv,
                  ("state", "code", "name", "size", "lines", "clipped",
                   "trust", "text"))
    else:
        print(f"  NO ENTRIES: {tree.get('code')} — {tree.get('detail')}")

    outside = WS.list_tree(cfg, "/etc")
    _show("list_tree('/etc')", outside, ("state", "code", "detail"))
    mut = WS.refused_mutation(cfg, "notes.md", action="write")
    _show("refused_mutation(write)", mut, ("state", "code", "detail"))
    print("\nRESULT: " + json.dumps({"sessions_total": total,
                                     "session_id": sid,
                                     "tree_shown": tree.get("shown"),
                                     "rc": rc}))
    return rc


if __name__ == "__main__":
    sys.exit(main())
