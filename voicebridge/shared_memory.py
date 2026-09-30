"""Unified Shared Memory — the single source of truth for Qubit.

Problem: the agent, worker, browser and local chat lanes each keep
separate chats. When you set a route and then give the *next*
instruction, the lane that answers has already forgotten the route.

Fix: a shared append-only ledger at ``<state_dir>/shared_memory.jsonl``
that every turn appends to — user utterances, routing decisions, agent
reports (full + summary), and local replies. On every dispatch the bridge
injects the recent ledger window into the task prompt so the *next* lane
sees the same story the previous one saw.

The ledger also powers the dashboard: /api/reports and /api/activity now
include shared history so switching lanes doesn't blank the feed.

File format: one JSON object per line::
  {"ts": 1714000000.0, "role": "user|assistant|system|agent",
   "text": "...", "route": "agent|worker|local|browser",
   "provider": "qubit|local", "session_key": "..."}

Historical rows may carry retired route names; readers treat route and
provider as opaque labels.

Kept small on read (last ~60 turns / ~8k chars) but never truncated on
write — history survives restarts and provider toggles.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class SharedMemory:
    """Persistent cross-lane ledger."""

    def __init__(self, state_dir: Path | str, max_chars: int = 8000, max_turns: int = 60) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "shared_memory.jsonl"
        self.max_chars = int(max_chars)
        self.max_turns = int(max_turns)

    # -- write --

    def append(self, role: str, text: str, route: str = "", provider: str = "",
               session_key: str = "", extra: Optional[Dict[str, Any]] = None) -> None:
        if not text or not text.strip():
            return
        rec: Dict[str, Any] = {
            "ts": time.time(),
            "role": role,
            "text": text.strip()[:12000],
            "route": route or "",
            "provider": provider or "",
            "session_key": session_key or "",
        }
        if extra:
            for k, v in extra.items():
                if k not in rec:
                    rec[k] = v
        try:
            with self.path.open("a", errors="replace") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                try:
                    fh.flush()
                    import os as _os
                    _os.fsync(fh.fileno())
                except Exception:
                    pass
        except OSError:
            pass

    def append_user(self, text: str, route: str = "") -> None:
        self.append("user", text, route=route)

    def append_assistant(self, text: str, route: str = "", provider: str = "") -> None:
        self.append("assistant", text, route=route, provider=provider)

    def append_vscode(self, text: str, workspace: str = "") -> None:
        self.append("vscode", text, route="vscode",
                    extra={"workspace": workspace} if workspace else None)

    def append_agent_report(self, summary: str, raw: str = "", provider: str = "",
                            session_key: str = "") -> None:
        # summaries are what the user heard; raw is kept for dashboard recall
        self.append("agent", summary or raw[:4000], route=provider or "agent",
                    provider=provider, session_key=session_key,
                    extra={"raw_len": len(raw)} if raw else None)

    # -- read --

    def recent(self, limit: int = 60) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(errors="replace").splitlines()[-max(limit*2, 200):]
        except OSError:
            return []
        out: List[Dict[str, Any]] = []
        for l in lines:
            try:
                out.append(json.loads(l))
            except Exception:
                continue
        return out[-limit:]

    def as_prompt(self, max_chars: int | None = None, max_turns: int | None = None) -> str:
        """Ledger formatted as `role: text` lines for injecting into the next agent prompt."""
        mc = int(max_chars or self.max_chars)
        mt = int(max_turns or self.max_turns)
        items = self.recent(limit=mt)
        if not items:
            return ""
        # build newest-first then reverse so oldest is first in the prompt
        lines: List[str] = []
        total = 0
        for rec in reversed(items):
            role = rec.get("role", "user")
            text = (rec.get("text") or "").strip()
            route = rec.get("route") or ""
            tag = f" [{route}]" if route and route not in (role, "") else ""
            line = f"{role}{tag}: {text}"
            if total + len(line) > mc:
                break
            lines.append(line)
            total += len(line) + 1
        lines.reverse()
        if not lines:
            return ""
        return "Recent shared history (same across agent/worker/browser/local):\n" + "\n".join(lines)

    def clear(self) -> None:
        try:
            self.path.write_text("", errors="replace")
        except OSError:
            pass
