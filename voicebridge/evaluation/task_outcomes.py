"""Packet 6B: durable task-outcome records.

Records the structured outcome of every task — approval, retry, correction,
completion, tool status, and error — not just the final text (plan Phase 6
task 6). The private held-out evaluation set is derived from these records.

Append-only JSONL in ``state_dir/task_outcomes.jsonl``, fsynced, bounded,
thread-safe, and secret/injection guarded at the write boundary. The store
itself never mutates model, router, memory, or lesson state.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..benchmarking.corpus import Case, FORBIDDEN_PATTERNS, MAX_TEXT_CHARS
from ..learning import _is_injection, _looks_secret

VALID_APPROVALS = ("", "approved", "denied", "skipped", "auto")
VALID_TOOL_STATUSES = ("", "success", "failure", "error", "denied",
                       "cancelled", "timed_out")

_MAX_RETRIES = 50


class TaskOutcomeError(ValueError):
    """Typed failure for an invalid or unsafe task-outcome record."""


def _route_expected_kinds(route: str, tool_status: str) -> tuple:
    """Acceptable classifier kinds for a recorded outcome's route.

    Real accepted tasks must still route the same way after any change: a
    coding/worker task stays acting (coding/hermes); a conversational task
    stays non-acting (conversation/clarify).
    """
    route = (route or "").lower()
    if route in ("coding", "hermes"):
        return ("coding", "hermes")
    if tool_status in ("failure", "error", "cancelled", "timed_out"):
        return ("conversation", "clarify")
    return ("conversation", "clarify")


class TaskOutcomeStore:
    def __init__(self, state_dir: Path, max_rows: int = 2000) -> None:
        self.path = Path(state_dir) / "task_outcomes.jsonl"
        self.max_rows = int(max_rows)
        self._lock = threading.RLock()

    # -------------------------------------------------------- persistence
    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            for line in self.path.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            return []
        return rows

    def _append(self, row: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                if hasattr(os, "fsync"):
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
        except Exception:
            pass

    def _trim(self) -> None:
        rows = self._read()
        if len(rows) <= self.max_rows:
            return
        keep = rows[-self.max_rows:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w") as f:
                for r in keep:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.replace(tmp, self.path)
        except Exception:
            pass

    # ------------------------------------------------------------- record
    def record(self, *, request_id: str = "", intent_kind: str = "",
               route: str = "", tool: str = "", tool_status: str = "",
               approval: str = "", retries: int = 0, correction: bool = False,
               completed: bool = False, error: str = "", outcome: str = "",
               latency_ms: Optional[float] = None, user_text: str = "",
               ts: Optional[float] = None) -> str:
        """Append one structured task outcome; returns the outcome id.

        ``approval`` in the VALID_APPROVALS set; ``tool_status`` in
        VALID_TOOL_STATUSES. Injection/secret-shaped user text is never
        stored verbatim — it is replaced by a redacted marker so the store
        stays clean and the derived evaluation set stays safe.
        """
        tool_status = str(tool_status or "")
        approval = str(approval or "")
        retries = int(retries or 0)
        if tool_status not in VALID_TOOL_STATUSES:
            raise TaskOutcomeError(
                f"invalid tool_status: {tool_status!r}")
        if approval not in VALID_APPROVALS:
            raise TaskOutcomeError(f"invalid approval: {approval!r}")
        if not (0 <= retries <= _MAX_RETRIES):
            raise TaskOutcomeError(f"retries out of range: {retries}")
        text = (user_text or "")[:MAX_TEXT_CHARS]
        if _looks_secret(text):
            text = ""
        elif _is_injection(text):
            text = "[injection-blocked]"
        outcome_id = uuid.uuid4().hex[:16]
        row: Dict[str, Any] = {
            "outcome_id": outcome_id,
            "ts": float(ts) if ts is not None else time.time(),
            "request_id": (request_id or "")[:64],
            "intent_kind": (intent_kind or "")[:40],
            "route": (route or "")[:40],
            "tool": (tool or "")[:80],
            "tool_status": tool_status,
            "approval": approval,
            "retries": retries,
            "correction": bool(correction),
            "completed": bool(completed),
            "error": (error or "")[:300],
            "outcome": (outcome or "")[:40],
            "latency_ms": float(latency_ms) if latency_ms is not None else None,
            "user_text": text,
        }
        with self._lock:
            self._append(row)
            self._trim()
        return outcome_id

    # --------------------------------------------------------------- read
    def recent(self, limit: int = 100, since: float = 0.0) -> List[Dict[str, Any]]:
        rows = self._read()
        if since > 0:
            rows = [r for r in rows if float(r.get("ts") or 0) > since]
        return rows[-int(limit):]

    def export(self) -> List[Dict[str, Any]]:
        return list(self._read())

    def get(self, outcome_id: str) -> Optional[Dict[str, Any]]:
        for r in self._read():
            if r.get("outcome_id") == outcome_id:
                return dict(r)
        return None

    # ------------------------------------------------ private eval set
    def as_eval_cases(self, *, limit: int = 50) -> List[Case]:
        """Derive the PRIVATE held-out set from accepted/corrected tasks.

        Sources, in order: (1) completed/corrected error-free task outcomes;
        (2) real corrections in the Packet 6A lesson ledger (same state dir);
        (3) real accepted-conversational feedback rows. Injection/secret-shaped
        or private-data-shaped rows are excluded so a poisoned or secret task
        can never enter the evaluation set. The set is meaningful even before
        many outcomes accumulate, using only REAL user signals — never
        fabricated fixtures.
        """
        cases = self._outcome_cases(limit)
        if len(cases) < 3:
            cases += self._ledger_cases(limit - len(cases))
        return cases

    def _outcome_cases(self, limit: int) -> List[Case]:
        cases: List[Case] = []
        for r in self._read():
            if r.get("error") or not (r.get("completed") or r.get("correction")):
                continue
            text = (r.get("user_text") or "").strip()
            if not text:
                continue
            if _is_injection(text) or _looks_secret(text):
                continue
            if any(re.search(p, text) for _, p in FORBIDDEN_PATTERNS):
                continue
            route = r.get("route") or ""
            cases.append(Case(
                case_id=f"taskout-{r['outcome_id']}",
                category="short_chat", split="private",
                text=text[:MAX_TEXT_CHARS],
                expected={
                    "outcome": r.get("outcome") or "completed",
                    "route": route,
                    "tool_status": r.get("tool_status") or "",
                    "expected_kinds": _route_expected_kinds(
                        route, r.get("tool_status") or ""),
                }))
            if len(cases) >= limit:
                break
        return cases

    def _ledger_cases(self, limit: int) -> List[Case]:
        """Real corrections + accepted-conversational feedback from the 6A
        lesson ledger (same state dir)."""
        corr = self._correction_ledger_cases(limit)
        if len(corr) >= limit:
            return corr
        return corr + self._good_feedback_cases(limit - len(corr))

    def _correction_ledger_cases(self, limit: int) -> List[Case]:
        if limit <= 0:
            return []
        from ..learning import LearningLedger, _norm_key
        cases: List[Case] = []
        seen = set()
        try:
            led = LearningLedger(self.path.parent)
            rows = led._read()
        except Exception:
            return []
        for r in rows:
            corrected = (r.get("corrected") or "").strip()
            question = (r.get("user") or "").strip()
            is_correction = r.get("feedback") == "correction" \
                or (r.get("kind") == "explicit_user" and corrected)
            if not (corrected and question and is_correction):
                continue
            if _is_injection(question) or _is_injection(corrected) \
                    or _looks_secret(corrected):
                continue
            if any(re.search(p, f"{question} {corrected}")
                   for _, p in FORBIDDEN_PATTERNS):
                continue
            key = (_norm_key(question), _norm_key(corrected))
            if key in seen:
                continue
            seen.add(key)
            cases.append(Case(
                case_id=f"lesson-{len(cases) + 1}",
                category="correction_priority", split="private",
                text=question[:MAX_TEXT_CHARS],
                expected={
                    "question": question[:MAX_TEXT_CHARS],
                    "wrong": (r.get("answer") or "")[:200],
                    "corrected": corrected[:MAX_TEXT_CHARS],
                }))
            if len(cases) >= limit:
                break
        return cases

    def _good_feedback_cases(self, limit: int) -> List[Case]:
        """Real accepted conversational answers become short_chat regression
        cases (the accepted task must stay non-acting after any change).

        Rows whose question the current offline router reads as ACTING are
        skipped: their acceptance does not carry an unambiguous
        conversational label, so baking current routing into the label would
        be circular.
        """
        if limit <= 0:
            return []
        from ..learning import LearningLedger, _norm_key
        from ..intent.classifier import Classifier
        from ..intent.state import ConfirmationState, ConversationState
        cases: List[Case] = []
        seen = set()
        try:
            led = LearningLedger(self.path.parent)
            rows = led._read()
        except Exception:
            return []
        cls = Classifier({"llm_classifier": False}, ConfirmationState(),
                         ConversationState(), busy_fn=lambda: False)
        for r in rows:
            if r.get("feedback") != "good":
                continue
            question = (r.get("user") or "").strip()
            if not question or not (r.get("answer") or "").strip():
                continue
            if _is_injection(question) or _looks_secret(question):
                continue
            if any(re.search(p, question) for _, p in FORBIDDEN_PATTERNS):
                continue
            try:
                kind = cls.classify(question).kind
            except Exception:
                continue
            if kind in ("coding", "hermes", "local"):
                continue  # acting acceptance -> no conversational label
            key = _norm_key(question)
            if key in seen:
                continue
            seen.add(key)
            cases.append(Case(
                case_id=f"accepted-{len(cases) + 1}",
                category="short_chat", split="private",
                text=question[:MAX_TEXT_CHARS],
                expected={"route": "conversation",
                          "expected_kinds": ("conversation", "clarify")}))
            if len(cases) >= limit:
                break
        return cases