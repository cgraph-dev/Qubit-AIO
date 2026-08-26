"""Fixed sanitized evaluation-corpus loading and validation (Packet 4A).

The corpus is a versioned, synthetic, sanitized data contract. Loading never
touches the network and never mutates configuration or runtime state.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

CORPUS_SCHEMA = "voicebridge.eval-corpus/1"
ACCEPTANCE_FILE = "acceptance_cases.json"
HOLDOUT_FILE = "heldout_cases.json"

CATEGORIES = (
    "deterministic_routing",
    "ambiguous_routing",
    "confirm_cancel_contextual",
    "local_vs_coding_safety",
    "malformed_speech",
    "short_chat",
    "personal_fact_conflict",
    "tool_selection",
    "invalid_arguments",
    "refusal_fallback",
    "language_policy",
    "prompt_injection",
)

MAX_TEXT_CHARS = 600

# Mechanical sanitization gate: shapes that must never appear in a committed
# fixture (real-mailbox providers, home paths, credential-shaped strings,
# long digit runs resembling phone/card numbers).
FORBIDDEN_PATTERNS = (
    ("personal_email", r"[A-Za-z0-9._%+-]+@(?:gmail|hotmail|outlook|yahoo|icloud|proton)\.(?:com|ro)"),
    ("home_path", r"/home/[A-Za-z0-9_.-]+"),
    ("api_key_shape", r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{15,}|AKIA[0-9A-Z]{16})\b"),
    ("long_digit_run", r"\b[0-9]{10,}\b"),
)


class CorpusError(ValueError):
    """Typed failure for invalid or unsafe corpus content."""


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    split: str
    text: str
    expected: Dict[str, Any] = field(default_factory=dict)


def _load_split_file(path: Path, expected_split: str) -> List[Case]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"missing corpus file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorpusError(f"{path.name}: top level must be an object")
    if raw.get("schema") != CORPUS_SCHEMA:
        raise CorpusError(
            f"{path.name}: schema {raw.get('schema')!r} != {CORPUS_SCHEMA!r}")
    if raw.get("split") != expected_split:
        raise CorpusError(
            f"{path.name}: split {raw.get('split')!r} != {expected_split!r}")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise CorpusError(f"{path.name}: 'cases' must be a non-empty list")
    cases: List[Case] = []
    for i, item in enumerate(cases_raw):
        if not isinstance(item, dict):
            raise CorpusError(f"{path.name}[{i}]: case must be an object")
        case_id = item.get("case_id")
        category = item.get("category")
        text = item.get("text")
        if not isinstance(case_id, str) or not case_id.strip():
            raise CorpusError(f"{path.name}[{i}]: missing case_id")
        if category not in CATEGORIES:
            raise CorpusError(
                f"{path.name}:{case_id}: unknown category {category!r}")
        if not isinstance(text, str) or not text.strip():
            raise CorpusError(f"{path.name}:{case_id}: missing text")
        if len(text) > MAX_TEXT_CHARS:
            raise CorpusError(
                f"{path.name}:{case_id}: text exceeds {MAX_TEXT_CHARS} chars")
        for name, pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                raise CorpusError(
                    f"{path.name}:{case_id}: forbidden pattern {name} "
                    "(corpus must stay sanitized)")
        expected = item.get("expected", {})
        if not isinstance(expected, dict):
            raise CorpusError(f"{path.name}:{case_id}: expected must be object")
        cases.append(Case(case_id=case_id, category=category,
                          split=expected_split, text=text, expected=expected))
    return cases


def load_corpus(corpus_dir: Path | str) -> Dict[str, List[Case]]:
    """Load both splits; validate separation and required coverage."""
    d = Path(corpus_dir)
    acceptance = _load_split_file(d / ACCEPTANCE_FILE, "acceptance")
    holdout = _load_split_file(d / HOLDOUT_FILE, "holdout")

    seen: Dict[str, str] = {}
    for c in acceptance + holdout:
        if c.case_id in seen:
            raise CorpusError(
                f"duplicate case_id across splits: {c.case_id}")
        seen[c.case_id] = c.split

    acc_cats = {c.category for c in acceptance}
    hold_cats = {c.category for c in holdout}
    missing_acc = [c for c in CATEGORIES if c not in acc_cats]
    missing_hold = [c for c in CATEGORIES if c not in hold_cats]
    if missing_acc:
        raise CorpusError(
            f"acceptance split missing categories: {missing_acc}")
    if missing_hold:
        raise CorpusError(
            f"holdout split missing categories: {missing_hold}")

    return {"acceptance": acceptance, "holdout": holdout}
