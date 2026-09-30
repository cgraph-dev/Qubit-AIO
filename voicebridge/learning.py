"""Learning ledger — Continuous Learning from Human Feedback (CLHF), inspired by
CopilotKit's self-learning agents, hardened with Packet 6A lesson provenance.

Stores durable "lessons" learned from the user over time — corrections, likes,
dislikes, and the user's confirmed preferences. These are injected into the local
model's prompt on every turn so the agent improves WITHOUT retraining.

Data (state_dir/learning.jsonl, fsynced, append-only evidence + rewritten
lesson rows):
  evidence rows  {"ev": <id>, "ts":..., "user":"...", "answer":"...",
                  "feedback":"good|bad|correction", "corrected":"...",
                  "kind":"observed_outcome"}
  lesson rows    {"lesson_id": <id>, "key": <normalized pattern>,
                  "pref"/"corrected"/"text": <content>, "kind": <class>,
                  "source": "correction|auto-good|voice|model-inference",
                  "evidence_ids": [...], "confidence": 0..1,
                  "created/updated/expires_at", "supersedes", "tombstoned",
                  "review_needed", "conflict_with", "history": [...]}

Signal separation (plan Phase 6 task 7):
  - explicit_user      : user corrections / add_preference — highest priority,
                         no TTL, no silent removal by outcomes;
  - observed_outcome   : promoted only from >=2 repeated consistent good
                         feedback; decays, expires, can be removed;
  - model_inference    : always staged as review items, NEVER in the prompt,
                         never auto-promoted; explicit user promotion required.

Conflict handling: contradictory evidence creates a review item (review_needed)
instead of silently choosing; an explicit correction supersedes stale lessons.
Prompt injection / secret-shaped content can never become a preference.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logging_setup import log_event
from .memory.store import _SECRET_SHAPES

# The three separated learning signals (plan Phase 6 task 7).
LESSON_KINDS = ("explicit_user", "observed_outcome", "model_inference")

MIN_PROMOTION_LIKES = 2          # repeated consistent good feedback required
DEFAULT_TTL_S = 90 * 86400       # observed-outcome lessons expire after 90 days
DECAY_HALF_LIFE_S = 30 * 86400   # confidence halves over this age
CONFIDENCE_FLOOR = 0.05          # below this a lesson stops being authoritative
PREF_BASE_CONFIDENCE = 0.6       # at exactly 2 likes
PREF_EXTRA_PER_LIKE = 0.05
PREF_CONFIDENCE_CAP = 0.9

# Prompt-injection / instruction-escalation shapes that must never become a
# durable preference. Kept narrow so ordinary preferences ("answer in English
# only") are never blocked.
_INJECTION_SHAPES = (
    r"(?i)\bignore\b.{0,40}\b(previous|prior|earlier|above|all)\b.{0,40}\b(instructions|messages|prompt|rules|commands)\b",
    r"(?i)\bignore\b.{0,30}\bsystem\b",
    r"(?i)\b(you are now|from now on you are|your new role is)\b",
    r"(?i)\bact (as if|as though|like) (you are|a)\b",
    r"(?i)\b(reveal|show|print|repeat|paste)\b.{0,20}\b(system|developer)\b.{0,20}\b(prompt|instructions)\b",
    r"(?i)\b(disable|turn off|remove|bypass|override)\b.{0,40}\b(safety|policy|confirmation|permission|approval|restriction|guardrail)\b",
    r"(?i)\b(change|modify|rewrite)\b.{0,20}\b(routing|policy|permissions|safety rules|capabilities|tool definitions|tools)\b",
    r"(?i)\bforget (all )?(your )?(instructions|rules|training|prompt)\b",
    r"(?i)\bforget\b.{0,40}\b(safety|policy|rules|instructions)\b",
    r"(?i)\b(rm -rf|drop table|:\(\)\{|mkfs\.|shutdown -h|sudo rm)\b",
    r"(?i)\b(run|execute)\b.{0,20}\b(rm -rf|:\(\)\{|mkfs|dd if=)\b",
    r"(?i)\byou have no (restrictions|limits|rules)\b",
    r"(?i)\bwithout (asking|confirmation|permission)\b.{0,20}$",
    r"(?i)\bnever ask (for permission|the user)\b",
    r"(?i)\baccess (the )?developer mode\b",
)
_INJECTION_RE = tuple(re.compile(p) for p in _INJECTION_SHAPES)
_SECRET_RE = tuple(re.compile(p) for p in _SECRET_SHAPES)


def _norm_key(text: str) -> str:
    """Loose normalization so 'what's my instagram?' ~ 'whats my instagram'."""
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def _is_injection(text: str) -> bool:
    return any(p.search(text or "") for p in _INJECTION_RE)


def _looks_secret(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_RE)


def _clean(text: str, limit: int) -> str:
    """Collapse whitespace for single-line prompt rendering."""
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


class LearningLedger:
    def __init__(self, state_dir: Path, max_lessons: int = 40) -> None:
        self.path = Path(state_dir) / "learning.jsonl"
        self.max_lessons = max_lessons
        self._lock = threading.RLock()

    # -------------------------------------------------------- persistence
    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
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

    def _append(self, row: dict) -> None:
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

    def _rewrite(self, rows: list[dict]) -> None:
        """Atomic rewrite of the ledger (lesson updates/removals)."""
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                if hasattr(os, "fsync"):
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
            os.replace(tmp, self.path)
        except Exception:
            if tmp is not None:
                try:
                    tmp.unlink()
                except Exception:
                    pass

    def _trim(self) -> None:
        rows = self._read()
        if len(rows) <= self.max_lessons * 4:
            return
        lessons = [r for r in rows if r.get("lesson_id")]
        evidence = [r for r in rows if not r.get("lesson_id")]
        keep = lessons + evidence[-self.max_lessons * 2:]
        if len(keep) > self.max_lessons * 6:
            keep = keep[-self.max_lessons * 6:]
        self._rewrite(keep)

    @staticmethod
    def _lesson_id(kind: str, key: str, content: str) -> str:
        return hashlib.sha256(
            f"{kind}|{_norm_key(key)}|{_norm_key(content)}".encode("utf-8")
        ).hexdigest()[:16]

    @staticmethod
    def _evidence_id() -> str:
        return uuid.uuid4().hex[:16]

    # ---------------------------------------------------------- feedback
    def record(self, user_text: str, answer: str, feedback: str,
               corrected: str = "") -> None:
        """`feedback` in (good | bad | correction). Appends an evidence row,
        then updates lessons: corrections supersede, good feedback may promote
        only after repeated consistent evidence, bad feedback lowers
        confidence. Injection/secret-shaped content never becomes a lesson."""
        fb = str(feedback or "").lower()
        if fb not in ("good", "bad", "correction"):
            raise ValueError("feedback must be good|bad|correction")
        corrected = (corrected or "").strip()
        with self._lock:
            evidence_id = self._evidence_id()
            self._append({
                "ev": evidence_id,
                "ts": time.time(),
                "user": (user_text or "")[:300],
                "answer": (answer or "")[:500],
                "feedback": fb,
                "corrected": (corrected or "")[:500],
                "kind": "observed_outcome",
            })
            if corrected and fb in ("correction", "bad"):
                self._apply_correction(evidence_id, user_text, corrected, answer)
            elif fb == "good":
                self._auto_preference_from_good(evidence_id, user_text, answer)
            elif fb == "bad":
                self._adjust_negative(evidence_id, user_text, answer)
            self._trim()

    # --------------------------------------------- explicit user correction
    def _apply_correction(self, evidence_id: str, user_text: str,
                          corrected: str, wrong: str) -> None:
        """Explicit corrections always win: supersede conflicting stale
        lessons, never blocked by outcomes, never expire."""
        corrected = (corrected or "").strip()
        if not corrected:
            return
        if _is_injection(corrected) or _is_injection(user_text) \
                or _looks_secret(corrected):
            log_event("learning.injection_blocked", kind="correction")
            return
        lid = self._lesson_id("explicit_user", user_text, corrected)
        key = _norm_key(user_text)
        now = time.time()
        rows = self._read()
        updated = False
        for i, r in enumerate(rows):
            if r.get("lesson_id") == lid:
                evs = list(r.get("evidence_ids") or [])
                if evidence_id not in evs:
                    evs.append(evidence_id)
                r["evidence_ids"] = evs
                r["updated_at"] = now
                r["corrected"] = corrected[:500]
                r["history"] = (r.get("history") or []) + [
                    {"action": "updated", "ts": now,
                     "payload": {"evidence_id": evidence_id}}]
                rows[i] = r
                updated = True
                break
        if not updated:
            superseded: List[str] = []
            for i, r in enumerate(rows):
                if not r.get("lesson_id") or r.get("lesson_id") == lid:
                    continue
                if r.get("tombstoned"):
                    continue
                if _norm_key(r.get("key") or r.get("user") or "") != key:
                    continue
                theirs = _norm_key(r.get("corrected") or r.get("answer")
                                   or r.get("pref") or "")
                if not theirs or theirs == _norm_key(corrected):
                    continue
                r["tombstoned"] = True
                r["enabled"] = False
                r["updated_at"] = now
                r["history"] = (r.get("history") or []) + [
                    {"action": "corrected_by", "ts": now,
                     "payload": {"lesson_id": lid}}]
                superseded.append(r.get("lesson_id"))
                rows[i] = r
            lesson: Dict[str, Any] = {
                "lesson_id": lid, "key": key, "ts": now, "updated_at": now,
                "user": (user_text or "")[:300],
                "corrected": corrected[:500],
                "wrong": (wrong or "")[:500],
                "kind": "explicit_user", "source": "correction",
                "evidence_ids": [evidence_id], "confidence": 1.0,
                "expires_at": None, "supersedes": superseded or None,
                "tombstoned": False, "enabled": True,
                "review_needed": False, "conflict_with": [],
                "history": [{"action": "add", "ts": now, "payload": None}],
            }
            rows.append(lesson)
        self._rewrite(rows)

    # ------------------------------------------- repeated-good promotion
    def _auto_preference_from_good(self, evidence_id: str, user_text: str,
                                   answer: str) -> None:
        """Promote a liked answer to a durable preference ONLY after >=2
        repeated consistent goods on the same pattern. One good interaction
        can never rewrite behavior. Contradictory evidence creates a review
        item instead of silently overriding."""
        key = _norm_key(user_text)
        if not key or not (answer or "").strip():
            return
        if _is_injection(user_text) or _is_injection(answer) \
                or _looks_secret(answer):
            log_event("learning.injection_blocked", kind="auto-good")
            return
        rows = self._read()
        likes = [r for r in rows
                 if r.get("feedback") == "good"
                 and _norm_key(r.get("user", "")) == key]
        if len(likes) < MIN_PROMOTION_LIKES:
            return
        pref = (f"For questions like \"{(user_text or '')[:80]}\", keep answering "
                f"like: {(answer or '')[:160]}")
        lid = self._lesson_id("observed_outcome", key, answer)
        conf = min(PREF_CONFIDENCE_CAP,
                   PREF_BASE_CONFIDENCE + PREF_EXTRA_PER_LIKE
                   * max(0, len(likes) - MIN_PROMOTION_LIKES))
        now = time.time()
        ev_ids = [r.get("ev") for r in likes if r.get("ev")]
        conflict = self._find_conflict(rows, key, answer, lid)
        updated = False
        for i, r in enumerate(rows):
            if r.get("lesson_id") == lid:
                evs = list(r.get("evidence_ids") or [])
                for e in ev_ids:
                    if e not in evs:
                        evs.append(e)
                r["evidence_ids"] = evs
                r["updated_at"] = now
                r["confidence"] = max(float(r.get("confidence") or conf), conf)
                r["history"] = (r.get("history") or []) + [
                    {"action": "reinforced", "ts": now,
                     "payload": {"likes": len(likes)}}]
                rows[i] = r
                updated = True
                break
        if not updated:
            rows.append({
                "lesson_id": lid, "key": key, "ts": now, "updated_at": now,
                "user": (user_text or "")[:300],
                "answer": (answer or "")[:500], "pref": pref,
                "kind": "observed_outcome", "source": "auto-good",
                "evidence_ids": ev_ids, "confidence": conf,
                "expires_at": now + DEFAULT_TTL_S,
                "supersedes": None, "tombstoned": False, "enabled": True,
                "review_needed": bool(conflict), "conflict_with": conflict,
                "history": [{"action": "promoted", "ts": now,
                             "payload": {"likes": len(likes)}}],
            })
        self._rewrite(rows)

    def _find_conflict(self, rows: list[dict], key: str, answer: str,
                       self_lid: str) -> List[str]:
        """Contradictory evidence for the same pattern -> review item, never a
        silent rewrite. Explicit corrections stay authoritative."""
        conflicts: List[str] = []
        for r in rows:
            if not r.get("lesson_id") or r.get("lesson_id") == self_lid:
                continue
            if r.get("tombstoned"):
                continue
            if _norm_key(r.get("key") or r.get("user") or "") != key:
                continue
            theirs = _norm_key(r.get("corrected") or r.get("answer")
                               or r.get("pref") or "")
            if not theirs or theirs == _norm_key(answer):
                continue
            conflicts.append(r["lesson_id"])
            if r.get("kind") == "explicit_user" \
                    and r.get("source") == "correction":
                # corrections stay authoritative; the NEW evidence is reviewed
                continue
            r["review_needed"] = True
            cw = list(r.get("conflict_with") or [])
            if self_lid not in cw:
                cw.append(self_lid)
            r["conflict_with"] = cw
            r["updated_at"] = time.time()
        return conflicts

    # -------------------------------------------------------- bad feedback
    def _adjust_negative(self, evidence_id: str, user_text: str,
                         answer: str) -> None:
        """Observed dislike lowers confidence and can remove an
        observed-outcome lesson; it may never silently remove an explicit
        lesson — it raises a review item instead."""
        key = _norm_key(user_text)
        if not key:
            return
        rows = self._read()
        now = time.time()
        changed = False
        for i, r in enumerate(rows):
            if not r.get("lesson_id") or r.get("tombstoned"):
                continue
            if _norm_key(r.get("key") or r.get("user") or "") != key:
                continue
            if r.get("kind") == "explicit_user":
                if not r.get("review_needed"):
                    r["review_needed"] = True
                    r["updated_at"] = now
                    r["history"] = (r.get("history") or []) + [
                        {"action": "negative_evidence", "ts": now,
                         "payload": {"evidence_id": evidence_id}}]
                    changed = True
                continue
            conf = float(r.get("confidence") or 0.5) - 0.2
            r["confidence"] = max(0.0, conf)
            r["updated_at"] = now
            r["history"] = (r.get("history") or []) + [
                {"action": "negative_evidence", "ts": now,
                 "payload": {"evidence_id": evidence_id}}]
            if conf < CONFIDENCE_FLOOR:
                r["tombstoned"] = True
                r["enabled"] = False
                r["history"] = (r.get("history") or []) + [
                    {"action": "removed_by_negative", "ts": now,
                     "payload": None}]
            changed = True
        if changed:
            self._rewrite(rows)

    # ------------------------------------------------------- preferences
    def add_preference(self, pref: str, source: str = "voice") -> None:
        """Explicit user preference: durable, no TTL, never expiry-driven."""
        pref = (pref or "").strip()
        if not pref:
            return
        if _is_injection(pref) or _looks_secret(pref):
            log_event("learning.injection_blocked", kind="explicit-preference")
            return
        with self._lock:
            lid = self._lesson_id("explicit_user", "", pref)
            now = time.time()
            rows = self._read()
            for i, r in enumerate(rows):
                if r.get("lesson_id") == lid:
                    r["updated_at"] = now
                    r["history"] = (r.get("history") or []) + [
                        {"action": "updated", "ts": now, "payload": None}]
                    rows[i] = r
                    self._rewrite(rows)
                    return
            rows.append({
                "lesson_id": lid, "key": "", "ts": now, "updated_at": now,
                "pref": pref[:300], "kind": "explicit_user", "source": source,
                "evidence_ids": [], "confidence": 1.0,
                "expires_at": None, "supersedes": None,
                "tombstoned": False, "enabled": True,
                "review_needed": False, "conflict_with": [],
                "history": [{"action": "add", "ts": now, "payload": None}],
            })
            self._rewrite(rows)

    # ------------------------------------------------ model-inference gate
    def stage_model_inference(self, text: str, *, confidence: float = 0.5,
                              evidence_ids: Optional[List[str]] = None,
                              source: str = "model-inference") -> str:
        """Stage a model-proposed preference. ALWAYS review-only: never in the
        prompt, never auto-promoted. Injection/secret-shaped inference is
        refused outright. Returns the staged lesson id."""
        text = (text or "").strip()
        if not text:
            raise ValueError("cannot stage empty model inference")
        if _is_injection(text) or _looks_secret(text):
            raise ValueError(
                "refusing to stage injection/secret-shaped model inference")
        with self._lock:
            lid = self._lesson_id("model_inference", "", text)
            now = time.time()
            rows = self._read()
            for i, r in enumerate(rows):
                if r.get("lesson_id") == lid:
                    evs = list(dict.fromkeys(
                        (r.get("evidence_ids") or [])
                        + list(evidence_ids or [])))
                    r["evidence_ids"] = evs
                    r["updated_at"] = now
                    rows[i] = r
                    self._rewrite(rows)
                    return lid
            rows.append({
                "lesson_id": lid, "key": "", "ts": now, "updated_at": now,
                "text": text[:300], "kind": "model_inference",
                "source": source,
                "evidence_ids": list(evidence_ids or []),
                "confidence": float(confidence),
                "expires_at": now + DEFAULT_TTL_S,
                "supersedes": None, "tombstoned": False, "enabled": True,
                "review_needed": True,  # staged: needs explicit user review
                "conflict_with": [],
                "history": [{"action": "staged_inference", "ts": now,
                             "payload": {"confidence": float(confidence)}}],
            })
            self._rewrite(rows)
            return lid

    def promote(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        """Explicit user promotion of a review/staged lesson. Only review
        items can be promoted; a normal active lesson is already authoritative."""
        with self._lock:
            rows = self._read()
            for i, r in enumerate(rows):
                if r.get("lesson_id") == lesson_id and not r.get("tombstoned"):
                    if not r.get("review_needed"):
                        return self.explain(lesson_id)
                    r["review_needed"] = False
                    r["updated_at"] = time.time()
                    r["history"] = (r.get("history") or []) + [
                        {"action": "promoted_by_user", "ts": time.time(),
                         "payload": None}]
                    rows[i] = r
                    self._rewrite(rows)
                    return self.explain(lesson_id)
            return None

    def remove(self, lesson_id: str) -> bool:
        """Explicit user removal: tombstone + audit; unrelated history kept."""
        with self._lock:
            rows = self._read()
            found = False
            for i, r in enumerate(rows):
                if r.get("lesson_id") == lesson_id:
                    r["tombstoned"] = True
                    r["enabled"] = False
                    r["updated_at"] = time.time()
                    r["history"] = (r.get("history") or []) + [
                        {"action": "removed", "ts": time.time(),
                         "payload": {"by": "user"}}]
                    rows[i] = r
                    found = True
            if found:
                self._rewrite(rows)
            return found

    # ---------------------------------------------- prompt / explanation
    def _effective_confidence(self, row: dict, now: Optional[float] = None) -> float:
        """Confidence decays over time toward a floor; explicit lessons do not
        decay. Used for ordering and for excluding decayed lessons."""
        now = now or time.time()
        if row.get("kind") == "explicit_user":
            return float(row.get("confidence") or 1.0)
        conf = float(row.get("confidence") or 0.5)
        age = max(0.0, now - float(row.get("ts") or now))
        return conf * (0.5 ** (age / DECAY_HALF_LIFE_S))

    def _active_lessons(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Latest version of every live lesson: not tombstoned, not superseded,
        not expired, not decayed below floor, not a pending review item."""
        now = now or time.time()
        latest: Dict[str, Dict[str, Any]] = {}
        for r in self._read():
            lid = r.get("lesson_id")
            if not lid:
                continue
            if r.get("tombstoned"):
                latest.pop(lid, None)
            else:
                latest[lid] = r
        out: List[Dict[str, Any]] = []
        for r in latest.values():
            exp = r.get("expires_at")
            if exp is not None and now > float(exp):
                continue
            if r.get("review_needed") and r.get("kind") != "explicit_user":
                # observed-outcome/model-inference review items stay out of the
                # prompt; explicit corrections stay authoritative until the
                # user removes or re-corrects them.
                continue
            if self._effective_confidence(r, now) < CONFIDENCE_FLOOR:
                continue
            out.append(r)
        return out

    def as_prompt(self, max_lessons: int = 8) -> str:
        """Render active lessons as a compact, bounded prompt block.

        Order: explicit corrections first (highest priority), then explicit
        preferences, then observed-outcome preferences by confidence. Review
        items and model inferences are never injected as authoritative."""
        now = time.time()
        lessons = self._active_lessons(now)
        if not lessons:
            return ""
        lessons.sort(key=lambda r: (
            0 if r.get("source") == "correction" else
            1 if r.get("kind") == "explicit_user" else 2,
            -self._effective_confidence(r, now)))
        lines: List[str] = []
        for r in lessons[:max_lessons]:
            if r.get("corrected"):
                lines.append(
                    f"- When asked \"{_clean(r.get('user', ''), 80)}\", the right "
                    f"answer is: {_clean(r['corrected'], 160)} (do NOT repeat the "
                    f"wrong version).")
            elif r.get("pref"):
                lines.append(f"- Preference: {_clean(r['pref'], 200)}")
            else:
                lines.append(f"- Lesson: {_clean(str(r.get('text') or r.get('pref') or ''), 200)}")
        if not lines:
            return ""
        block = "\n".join(lines[:max_lessons])
        return ("<lessons-what-the-user-taught-me>\n"
                "These are facts/preferences the user explicitly taught me. They are "
                "ALWAYS true and take priority over older answers.\n"
                f"{block}\n</lessons-what-the-user-taught-me>")

    def explain(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        """Structured provenance for one lesson — stored facts only, never a
        model-invented explanation."""
        rows = self._read()
        row = None
        for r in rows:  # append-only: the newest version of the lesson wins
            if r.get("lesson_id") == lesson_id:
                row = r
        if row is None:
            return None
        now = time.time()
        return {
            "lesson_id": row["lesson_id"],
            "kind": row.get("kind"),
            "source": row.get("source"),
            "text": (row.get("pref") or row.get("corrected")
                     or row.get("text") or ""),
            "pattern": row.get("user") or row.get("key") or "",
            "confidence": round(self._effective_confidence(row, now), 3),
            "raw_confidence": float(row.get("confidence") or 0.0),
            "evidence_ids": list(row.get("evidence_ids") or []),
            "evidence_count": len(row.get("evidence_ids") or []),
            "created_at": row.get("ts"),
            "updated_at": row.get("updated_at"),
            "expires_at": row.get("expires_at"),
            "decayed": bool(row.get("expires_at")) or self._effective_confidence(
                row, now) < float(row.get("confidence") or 0.0),
            "supersedes": row.get("supersedes"),
            "tombstoned": bool(row.get("tombstoned")),
            "review_needed": bool(row.get("review_needed")),
            "conflict_with": list(row.get("conflict_with") or []),
            "history": list(row.get("history") or []),
        }

    def lessons(self) -> List[Dict[str, Any]]:
        return [self.explain(r["lesson_id"]) for r in self._active_lessons()]

    def reviews(self) -> List[Dict[str, Any]]:
        """Review items: contradictory evidence or staged model inferences."""
        now = time.time()
        latest: Dict[str, Dict[str, Any]] = {}
        for r in self._read():
            lid = r.get("lesson_id")
            if not lid:
                continue
            if r.get("tombstoned"):
                latest.pop(lid, None)
            else:
                latest[lid] = r
        return [self.explain(r["lesson_id"])
                for r in latest.values() if r.get("review_needed")]