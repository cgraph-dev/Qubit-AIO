"""HUD sudo-governance collector — Qubit-native re-point of
``backend/collectors/sudo.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package.

DECISION: MAP (Qubit has an equivalent authority with a different shape)
-------------------------------------------------------------------------
Upstream's Sudo panel is four things: (1) an approvals/security config block,
(2) a count of ``sudo`` commands it finds in ``messages`` via an FTS MATCH,
(3) an inference of each command's OUTCOME from a tool-result JSON blob
(``exit_code`` / a "password required" regex), and (4) approved commands
scraped out of ``gateway.log`` prose. Evidence for the mapping:

* (1) is real and Qubit has it: :mod:`voicebridge.safety.policy` treats
  ``\\bsudo\\b`` as a destructive pattern requiring confirmation, and the
  approvals journal carries the approval mode/ttl. Those are read here.
* (2) upstream opened ``state.db`` itself and FTS-joined ``messages``. This
  tree forbids that (826 MB, single-writer), so anything transcript-shaped
  goes through :class:`voicebridge.qubit_session_store.QubitSessionStore`,
  which returns role/content/timestamp/meta rows and NO exit code. So the
  text-level ``sudo`` occurrences are counted through the authority and
  reported as OCCURRENCES — never as "commands that ran" — and the outcome
  axis is NOT inferred.
* (3)/(4) are the inference the previous lane was right to refuse: a regex
  over a tool blob and a scrape of log prose are guesses dressed as outcomes.
  Qubit instead has the thing upstream was trying to reconstruct: a durable
  append-only confirmation journal (:mod:`voicebridge.approvals`, JSONL at
  ``<state_dir>/approvals.jsonl``) that records, per capability, the exact
  state machine (in_progress -> executing -> complete | denied | aborted).
  That IS the sudo governance ledger for this product — an approved
  ``sudo``-class command went through ``respond(approve)`` then
  ``finish()`` — so the panel counts real decisions per state instead of
  guessing them from prose.

So the panel answers the question upstream's panel was asking (what did this
agent get approval for, and what did it run without one?) from a state machine
Qubit actually keeps. ``surfaced_in_transcripts`` and ``journal`` are reported
separately and never summed: they measure different things.

Read-only: ``ApprovalJournal`` is opened for reading (it loads the journal in
``__init__`` and this module only calls ``pending`` / ``history``). Nothing is
created, responded to, or finished. Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The confirmation trigger upstream's panel is really about. Kept identical to
#: :data:`voicebridge.safety.policy.DESTRUCTIVE_PATTERNS`' sudo rule so the panel
#: and the live policy can never disagree about what needs an approval.
SUDO_PATTERN = re.compile(r"\bsudo\b", re.IGNORECASE)

#: Capability prefixes the journal uses for command-shaped work. Reported
#: separately from the sudo-transcript count; see the module docstring.
COMMAND_PREFIXES = ("exec.", "shell.", "voice.exec", "terminal.")

#: Transcript occurrences is a scan, not a full-text index (the authority has
#: no FTS read seam). Bounded so the panel never walks 1000 sessions' messages.
SCAN_SESSIONS = 40
SCAN_MESSAGES = 400

TERMINAL_STATES = ("complete", "denied", "aborted")


def _state(state: Any) -> str:
    return str(state or "").strip().lower()


def _journal_path(cfg: Any) -> Optional[Path]:
    try:
        return Path(cfg.state_dir()) / "approvals.jsonl"
    except Exception:  # noqa: BLE001 - no cfg means no journal, not a crash
        return None


def _iso(ts: Any) -> Optional[str]:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            return datetime.fromtimestamp(float(ts)).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(ts, str) and ts:
        return ts
    return None


def _policy(cfg: Any) -> Dict[str, Any]:
    """The sudo-relevant half of the live safety policy.

    ``requires_confirmation`` is not re-derived here: it is
    :meth:`voicebridge.safety.policy.SafetyPolicy.check`'s own answer for the
    string ``sudo apt-get install -y ripgrep`` against the LIVE config, so a
    user who added ``sudo`` to ``never_confirm`` sees that reflected instead of
    a hard-coded claim.
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "pattern": "\\bsudo\\b",
        "requires_confirmation": None,
        "confirm_reason": "",
        "approval_mode": "",
        "approval_timeout_s": None,
    }
    data = getattr(cfg, "data", None)
    approvals = {}
    if isinstance(data, dict):
        raw = data.get("approvals")
        if isinstance(raw, dict):
            approvals = raw
            out["approval_mode"] = str(raw.get("mode", "") or "")
            try:
                out["approval_timeout_s"] = int(raw.get("timeout") or 0) or None
            except (TypeError, ValueError):
                out["approval_timeout_s"] = None
    try:
        from ..safety.policy import SafetyPolicy

        policy = SafetyPolicy(getattr(cfg, "section", lambda _n: {})("safety")
                              or {})
        verdict = policy.check("sudo apt-get install -y ripgrep")
        out["requires_confirmation"] = bool(verdict.require_confirm)
        out["confirm_reason"] = str(getattr(verdict, "confirm_reason", "") or "")
        out["available"] = True
        out["detail"] = "live voicebridge.safety.policy"
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"SafetyPolicy unavailable ({type(exc).__name__}: {exc})"
    return out


def _journal(cfg: Any, limit: int) -> Dict[str, Any]:
    """Approval-journal summary: real decisions per terminal state.

    ``available`` is False when the journal cannot be opened, with the reason;
    an ABSENT journal file is ``available: True`` with zero counts (that is a
    real answer: nothing was ever confirmed on this box).
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "path_present": False,
        "pending": 0,
        "by_state": {},
        "by_capability": [],
        "recent": [],
        "total_terminal": 0,
        "authority": "voicebridge.approvals.ApprovalJournal",
    }
    path = _journal_path(cfg)
    if path is None:
        out["detail"] = "the live config exposes no state directory"
        return out
    out["path_present"] = path.is_file()
    try:
        from ..approvals import ApprovalJournal

        journal = ApprovalJournal(path, default_ttl_s=120.0)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"approval journal unreadable ({type(exc).__name__}: {exc})"
        return out
    try:
        pending = journal.pending()
        history = journal.history(limit=max(1, int(limit)))
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"approval journal read failed ({type(exc).__name__}: {exc})"
        return out

    items = list(history.get("items") or [])
    states: Counter = Counter()
    caps: Counter = Counter()
    recent: List[Dict[str, Any]] = []
    for row in items:
        st = _state(getattr(row, "state", ""))
        states[st] += 1
        caps[str(getattr(row, "capability", "") or "")] += 1
    for row in items[:max(1, int(limit))]:
        recent.append({
            "id": str(getattr(row, "id", "") or "")[:32],
            "capability": str(getattr(row, "capability", "") or ""),
            "summary": str(getattr(row, "summary", "") or "")[:240],
            "state": _state(getattr(row, "state", "")),
            "decision": str(getattr(row, "decision", "") or ""),
            "created_at": _iso(getattr(row, "created_at", None)),
            "settled_at": _iso(getattr(row, "settled_at", None)),
            "actor": str(((getattr(row, "meta", None) or {}) or {}).get(
                "actor", "") or ""),
        })

    total_terminal = int(history.get("total") or 0)
    out.update(
        available=True,
        detail=(f"{len(pending)} pending, {total_terminal} settled "
                f"record(s) in the confirmation journal"),
        pending=len(pending),
        by_state=dict(states),
        by_capability=[{"capability": c or "(unnamed)", "count": n}
                       for c, n in caps.most_common(10)],
        recent=recent,
        total_terminal=total_terminal,
    )
    return out


def _transcripts(cfg: Any) -> Dict[str, Any]:
    """Count ``sudo`` OCCURRENCES in recent transcripts, through the authority.

    Deliberately named ``occurrences``: this is a bounded text scan of
    role/content rows the session authority publishes, so it counts mentions of
    sudo in a conversation, NOT commands that executed. No outcome is inferred
    because the authority does not publish one.
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "occurrences": 0,
        "sessions_scanned": 0,
        "sessions_with_sudo": 0,
        "by_role": {},
        "samples": [],
        "authority": "voicebridge.qubit_session_store.QubitSessionStore",
    }
    try:
        from ..qubit_session_store import QubitSessionStore

        store = QubitSessionStore(cfg)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"session authority unavailable ({type(exc).__name__}: {exc})"
        return out
    try:
        page = store.list(limit=SCAN_SESSIONS)
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"session list failed ({type(exc).__name__}: {exc})"
        return out
    rows = page.get("sessions") if isinstance(page, dict) else None
    if not isinstance(rows, list):
        out["detail"] = "the session authority returned no session list"
        return out

    roles: Counter = Counter()
    hit_sessions = 0
    samples: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "")
        if not sid:
            continue
        try:
            msgs = store.messages(sid, limit=SCAN_MESSAGES)
        except Exception:  # noqa: BLE001 - one session is not the panel
            continue
        out["sessions_scanned"] += 1
        found_here = False
        for msg in (msgs.get("messages") or []) if isinstance(msgs, dict) else []:
            if not isinstance(msg, dict):
                continue
            body = msg.get("content")
            if not isinstance(body, str) or "sudo" not in body.lower():
                continue
            count = len(SUDO_PATTERN.findall(body))
            if not count:
                continue
            found_here = True
            roles[str(msg.get("role") or "unknown")] += count
            out["occurrences"] += count
            if len(samples) < 25:
                idx = body.lower().find("sudo")
                start = max(0, idx - 40)
                excerpt = body[start:start + 140].replace("\n", " ").strip()
                samples.append({
                    "session_id": sid,
                    "session_title": str(row.get("title") or ""),
                    "role": str(msg.get("role") or "unknown"),
                    "excerpt": excerpt,
                    "count": count,
                })
        if found_here:
            hit_sessions += 1

    out.update(
        available=True,
        detail=(f"bounded scan of the {out['sessions_scanned']} most recent "
                f"sessions; mentions, not executions"),
        by_role=dict(roles),
        sessions_with_sudo=hit_sessions,
        samples=samples,
    )
    return out


def collect_sudo(home: Optional[Any] = None, cfg: Any = None, *,
                 limit: int = 20) -> Dict[str, Any]:
    """Sudo governance: the live policy rule, the journal, and transcript mentions.

    ``home`` is accepted for call-site symmetry with the other collectors and
    unused: every read is over *cfg*'s own state directory and the session
    authority, both of which already resolve the Qubit home.
    """
    policy = _policy(cfg)
    journal = _journal(cfg, limit)
    transcripts = _transcripts(cfg)
    available = bool(policy.get("available") or journal.get("available")
                     or transcripts.get("available"))
    return {
        "available": available,
        "detail": journal.get("detail") or policy.get("detail") or "",
        "policy": policy,
        "journal": journal,
        "transcripts": transcripts,
        "limit": max(1, int(limit)),
        "authority": "voicebridge.safety.policy + voicebridge.approvals + "
                     "voicebridge.qubit_session_store",
    }


__all__ = ["collect_sudo", "SUDO_PATTERN"]
