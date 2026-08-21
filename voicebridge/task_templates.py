"""Daily-task templates: spoken phrase -> known OpenClaw/Hermes task text.

The idea behind this module (and the reason it exists BEFORE the intent
classifier): the user's recurring daily tasks should not cost a model
round-trip. A template match dispatches the SAME battle-tested task text
every time — deterministic, instant, zero tokens — and the local model
stays free for actual conversation.

Template file: config/task-templates.yaml (installed to
~/.config/openclaw-voice-bridge/task-templates.yaml). Format:

    templates:
      - name: check-projects-status
        match:                      # regex, case-insensitive, any() wins
          - "check (my )?(projects|repos) status"
          - "status of (my )?projects"
        kind: coding                # coding | hermes | conversation
        task: "List the user's main projects under ~/Projects/Repos and report their git status, current branch, and any uncommitted changes. Keep it to a concise summary."
        ack: "Checking your projects."   # spoken immediately
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .logging_setup import log_event


@dataclass
class TemplateMatch:
    name: str
    kind: str            # coding | hermes | conversation
    task: str
    ack: str = ""
    confidence: float = 0.9
    reason: str = "task template"


@dataclass
class TaskTemplates:
    items: List[Dict[str, Any]] = field(default_factory=list)
    _compiled: List[tuple] = field(default_factory=list, init=False)

    @classmethod
    def load(cls, path: Optional[Path | str] = None,
             defaults_path: Optional[Path | str] = None) -> "TaskTemplates":
        """Load user templates (path), merged over repo defaults (defaults_path)."""
        items: List[Dict[str, Any]] = []
        if defaults_path and Path(defaults_path).exists():
            items.extend(_parse_yaml(defaults_path))
        if path and Path(path).exists():
            items.extend(_parse_yaml(path))
        tt = cls(items=items)
        tt._compile()
        return tt

    def _compile(self) -> None:
        self._compiled = []
        for it in self.items:
            pats = it.get("match") or []
            if isinstance(pats, str):
                pats = [pats]
            for p in pats:
                try:
                    self._compiled.append((re.compile(p, re.I), it))
                except re.error as exc:
                    log_event("task_template.bad_pattern", pattern=p, error=str(exc)[:80])

    def match(self, text: str) -> Optional[TemplateMatch]:
        low = text.strip().lower()
        for rx, it in self._compiled:
            if rx.search(low):
                return TemplateMatch(
                    name=it.get("name", "template"),
                    kind=it.get("kind", "coding"),
                    task=it.get("task", text),
                    ack=it.get("ack", ""),
                    reason=f"task template: {it.get('name', 'template')}",
                )
        return None


def _parse_yaml(path: Path | str) -> List[Dict[str, Any]]:
    try:
        d = yaml.safe_load(Path(path).read_text(errors="replace")) or {}
        return d.get("templates", []) or []
    except Exception as exc:
        log_event("task_template.load.error", path=str(path), error=str(exc)[:120])
        return []
