"""HUD patterns collector — Qubit-native port of ``backend/collectors/patterns.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: PORT (Qubit publishes the inputs; the analysis is upstream's)
----------------------------------------------------------------------
The previous lane marked this GAP ("Qubit records no pattern-mining events").
That confuses an event log with an aggregation. Upstream mines nothing durable
either: ``_do_collect_patterns`` runs four SQL aggregates over its own
``sessions``/``messages`` tables at request time. Every input it needs is
published by :class:`voicebridge.qubit_session_store.QubitSessionStore`:

+-----------------------+--------------------------------------------------+
| upstream input        | Qubit authority (``list()`` / ``messages()``)     |
+=======================+==================================================+
| ``sessions.title``    | ``title``                                         |
| first user message    | first ``messages()`` row with role ``user``       |
| ``message_count``     | ``message_count``                                 |
| ``tool_call_count``   | ``tool_call_count``                               |
| ``started_at``        | ``started_at``                                    |
| ``SUM(input_tokens)`` | session rows' ``input_tokens`` / ``output_tokens``|
+-----------------------+--------------------------------------------------+

What is ported unchanged: the task-cluster keyword table, ``_classify``,
``_normalize_prompt``, repeated-prompt detection (a prompt seen >= 2x,
``could_be_skill`` at >= 3), and the session-hour histogram.

What is NOT ported, and why
---------------------------
``tool_workflows`` (``_top_trigrams``) needs an ordered per-session sequence of
TOOL NAMES. The authority's message rows carry ``{role, content, timestamp,
meta}`` — no ``tool_calls`` JSON — exactly as on the Replay panel. So the field
is returned as ``None`` with ``tool_workflows_reason`` naming the missing
column, and the panel says so. An empty list would read as "no workflows
found", which would be a lie about a different thing.

Method difference worth stating on screen: upstream grouped by
``strftime('%H', started_at)`` in SQL. The authority has no SQL seam, so the
histogram is computed in Python from the same ``started_at`` values —
identical answer, one extra pass, and the bounded window is named
(``sessions_scanned``) so "1 session at 03:00" is never read as a total.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .collectors_corrections import _open_ro, _store_path

#: One page of sessions to analyse. The authority caps ``list()`` at 1000;
#: mining is bounded so the tab cannot become a full-store walk.
SCAN_SESSIONS = 300
#: Repeated-prompt rows shown.
REPEATED_LIMIT = 15
#: Clusters shown (upstream's own cutoff).
CLUSTER_LIMIT = 10
#: Transcripts read for the first user message. Bounded: one call per session
#: would be 300 authority reads on every poll, so the window is smaller.
FIRST_MESSAGE_SESSIONS = 120
FIRST_MESSAGE_LIMIT = 3

# Upstream's table, verbatim: first match wins.
_CLUSTERS: List[tuple] = [
    ("git ops", ["commit", "push", "pull", "merge", "branch", "rebase",
                 " pr ", "pull request", "release", "tag", "stash"]),
    ("debugging", ["fix", "bug", "error", "broken", "failing", "crash",
                   "traceback", "exception", "not work", "doesn't work"]),
    ("code gen", ["create", "implement", "add feature", "build", "write a",
                  "new function", "new class", "generate"]),
    ("refactor", ["refactor", "rename", "clean up", "simplify", "extract",
                  "reorganize", "restructure", "move"]),
    ("research", ["explain", "how does", "what is", "what are", "find",
                  "search", "look at", "investigate", "understand"]),
    ("config/ops", ["install", "configure", "setup", "deploy", "env",
                    "systemd", "cron", "docker", "service"]),
    ("docs", ["readme", "documentation", "comment", "docstring", "document"]),
]


def _classify(text: str) -> str:
    lower = text.lower()
    for label, keywords in _CLUSTERS:
        if any(kw in lower for kw in keywords):
            return label
    return "other"


def _normalize_prompt(text: str) -> str:
    return text.strip().lower()[:80]


def _hour(ts: Any) -> Optional[int]:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(float(ts)).hour
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _first_user_message(store: Any, sid: str) -> str:
    try:
        page = store.messages(sid, limit=FIRST_MESSAGE_LIMIT)
    except Exception:  # noqa: BLE001 - one session is not the panel
        return ""
    for msg in (page.get("messages") or []) if isinstance(page, dict) else []:
        if isinstance(msg, dict) and str(msg.get("role")) == "user":
            return str(msg.get("content") or "")
    return ""


def _token_totals(cfg: Any) -> Dict[str, Any]:
    """Session token sums, per day bucket, from the store's own aggregate.

    ``analytics_usage`` is the SAME function ``GET /api/analytics`` serves, so
    the Patterns tab and the Costs tab can never disagree about a day's tokens.
    """
    out: Dict[str, Any] = {"available": False, "detail": "", "daily": [],
                           "source": "voicebridge.qubit_session_store."
                                     "analytics_usage"}
    try:
        from ..qubit_session_store import analytics_usage

        usage = analytics_usage(cfg, days=30)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out
    if not isinstance(usage, dict):
        out["detail"] = "no usage recorded yet"
        out["available"] = True
        return out
    out["available"] = True
    out["daily"] = [
        {"day": str(row.get("day") or ""),
         "sessions": int(row.get("sessions") or 0),
         "tokens": int((row.get("input_tokens") or 0))
                   + int((row.get("output_tokens") or 0))}
        for row in usage.get("daily") or [] if isinstance(row, dict)
    ]
    return out


def collect_patterns(home: Optional[Any] = None, cfg: Any = None, *,
                     scan: int = SCAN_SESSIONS) -> Dict[str, Any]:
    """Prompt clusters, repeated prompts, the hour histogram and daily tokens."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "sessions_scanned": 0,
        "total_sessions": 0,
        "clusters": [],
        "repeated_prompts": [],
        "hourly_activity": [],
        "tool_workflows": None,
        "tool_workflows_reason": "",
        "daily_tokens": {"available": False, "daily": []},
        "authority": "voicebridge.qubit_session_store.QubitSessionStore",
    }
    try:
        from ..qubit_session_store import QubitSessionStore

        store = QubitSessionStore(cfg)
        page = store.list(limit=max(1, int(scan)))
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"session authority unavailable ({type(exc).__name__}: {exc})"
        return out
    rows = page.get("sessions") if isinstance(page, dict) else None
    if not isinstance(rows, list):
        out["detail"] = "the session authority returned no session list"
        return out

    buckets: Dict[str, Dict[str, Any]] = {
        label: {"count": 0, "msg_sum": 0, "tool_sum": 0, "titles": []}
        for label, _ in _CLUSTERS}
    buckets["other"] = {"count": 0, "msg_sum": 0, "tool_sum": 0, "titles": []}
    prompts: Counter = Counter()
    last_seen: Dict[str, float] = {}
    hours: Counter = Counter()
    hour_msgs: Counter = Counter()

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "")
        title = str(row.get("title") or row.get("name") or "")
        first = ""
        if sid and i < FIRST_MESSAGE_SESSIONS:
            first = _first_user_message(store, sid)
        if not first:
            first = str(row.get("preview") or "")
        out["sessions_scanned"] += 1

        label = _classify(f"{first} {title}")
        bucket = buckets[label]
        bucket["count"] += 1
        bucket["msg_sum"] += int(row.get("message_count") or 0)
        bucket["tool_sum"] += int(row.get("tool_call_count") or 0)
        if (first or title) and len(bucket["titles"]) < 3:
            bucket["titles"].append((title or first)[:50])

        if first:
            norm = _normalize_prompt(first)
            prompts[norm] += 1
            started = row.get("started_at")
            try:
                started_f = float(started) if started is not None else 0.0
            except (TypeError, ValueError):
                started_f = 0.0
            if norm not in last_seen or started_f > last_seen[norm]:
                last_seen[norm] = started_f

        h = _hour(row.get("started_at"))
        if h is not None:
            hours[h] += 1
            hour_msgs[h] += int(row.get("message_count") or 0)

    clusters = []
    for label, b in buckets.items():
        if not b["count"]:
            continue
        clusters.append({
            "label": label,
            "count": b["count"],
            "avg_messages": round(b["msg_sum"] / b["count"], 2),
            "avg_tool_calls": round(b["tool_sum"] / b["count"], 2),
            "example_titles": b["titles"],
        })
    clusters.sort(key=lambda c: -c["count"])

    repeated = []
    for norm, count in prompts.most_common(REPEATED_LIMIT):
        if count < 2:
            break
        ts = last_seen.get(norm) or 0.0
        repeated.append({
            "pattern": norm,
            "count": count,
            "last_seen": (datetime.fromtimestamp(ts, tz=timezone.utc)
                          .isoformat() if ts else None),
            "could_be_skill": count >= 3,
        })

    out.update(
        available=True,
        detail=(f"analysed the {out['sessions_scanned']} most recent "
                f"sessions; first-message text read for the newest "
                f"{min(out['sessions_scanned'], FIRST_MESSAGE_SESSIONS)}"),
        total_sessions=int((page or {}).get("total") or len(rows))
        if isinstance(page, dict) else len(rows),
        clusters=clusters[:CLUSTER_LIMIT],
        repeated_prompts=repeated,
        hourly_activity=[{"hour": h, "sessions": hours.get(h, 0),
                          "messages": hour_msgs.get(h, 0)} for h in range(24)],
        tool_workflows=None,
        tool_workflows_reason=(
            "Tool-sequence mining needs an ordered per-session list of tool "
            "names. The session authority publishes role/content/timestamp "
            "per message and a per-session tool_call_count, but no tool_call "
            "records, so no sequence can be reconstructed without inventing "
            "one."),
    )
    out["daily_tokens"] = _token_totals(cfg)
    return out


__all__ = ["collect_patterns"]
