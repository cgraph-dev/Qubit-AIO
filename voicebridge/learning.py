"""Learning ledger — Continuous Learning from Human Feedback (CLHF), inspired by
CopilotKit's self-learning agents.

Stores durable "lessons" learned from the user over time — corrections, likes,
dislikes, and the user's confirmed preferences. These are injected into the local
model's prompt on every turn so the agent improves WITHOUT retraining.

Data (state_dir/learning.jsonl, fsynced):
  {"ts":..., "user":"happy you got that", "answer":"...", "feedback":"good"}
  {"ts":..., "user":"what's my instagram", "answer":"I can't access it", "feedback":"bad", "corrected":"It's @tr.cky"}
  {"ts":..., "pref":"answer so-and-so", "enabled":true, "source":"voice"}

Rules surfaced as prompt text are the DISTILLED good feedback + corrections:
  because the LLM can't measure, we keep the raw recent signals and let the model
  use the explicit <lessons> block to avoid repeating known mistakes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class LearningLedger:
    def __init__(self, state_dir: Path, max_lessons: int = 40) -> None:
        self.path = Path(state_dir) / "learning.jsonl"
        self.max_lessons = max_lessons

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
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
                import os as _os
                if hasattr(_os, "fsync"):
                    try:
                        _os.fsync(f.fileno())
                    except Exception:
                        pass
        except Exception:
            pass

    def record(self, user_text: str, answer: str, feedback: str,
               corrected: str = "") -> None:
        """`feedback` in (good | bad | correction). Stores a lesson row."""
        self._append({
            "ts": time.time(),
            "user": (user_text or "")[:300],
            "answer": (answer or "")[:500],
            "feedback": feedback,
            "corrected": (corrected or "")[:500],
        })
        # keep the ledger bounded
        rows = self._read()
        if len(rows) > self.max_lessons * 4:
            try:
                keep = rows[-self.max_lessons * 4:]
                self.path.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in keep) + "\n",
                    errors="replace")
            except Exception:
                pass

    def as_prompt(self, max_lessons: int = 8) -> str:
        """Render durable lessons / corrections as a compact prompt block."""
        rows = self._read()
        if not rows:
            return ""
        # Useful lessons = explicit corrections, or 'bad' feedback with a corrected
        # answer, or explicit preferences (pref/enabled).
        lessons: list[str] = []
        for r in rows[-max_lessons * 3:]:
            if r.get("corrected"):
                lessons.append(
                    f"- When asked \"{r.get('user','')[:80]}\", the right answer is: "
                    f"{r['corrected'][:160]} (do NOT repeat the wrong version).")
            elif r.get("feedback") == "bad" and r.get("answer"):
                lessons.append(
                    f"- The user disliked this answer to \"{r.get('user','')[:80]}\": "
                    f"\"{r['answer'][:120]}\". Avoid repeating it.")
            elif r.get("feedback") == "good" and r.get("answer"):
                lessons.append(
                    f"- The user LIKED this answer to \"{r.get('user','')[:80]}\": "
                    f"\"{r['answer'][:120]}\". Keep answering this way.")
            elif r.get("pref"):
                lessons.append(f"- Preference: {r['pref'][:200]}")
        if not lessons:
            return ""
        block = "\n".join(lessons[-max_lessons:])
        return ("<lessons-what-the-user-taught-me>\n"
                "These are facts/preferences the user explicitly taught me. They are "
                "ALWAYS true and take priority over older answers.\n"
                f"{block}\n</lessons-what-the-user-taught-me>")

    def add_preference(self, pref: str, source: str = "voice") -> None:
        self._append({"ts": time.time(), "pref": (pref or "")[:300], "enabled": True,
                      "source": source})
