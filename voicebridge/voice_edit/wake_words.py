"""Global voice wake-word list (Qubit-owned), persisted atomically.

One small JSON file under the canonical Qubit state dir (never the repo)
holding the single global trigger list — the same shape the upstream
reference uses (``{"triggers": [...], "updatedAtMs": ...}``), ported from
OpenClaw's ``voicewake`` store and its
``normalizeVoiceWakeTriggerWord`` matcher semantics. Wake words are global:
there are no per-node/per-app lists.

Every read fails closed to :data:`DEFAULT_TRIGGERS` (missing / unreadable /
corrupt / empty / junk), every write normalizes, dedupes, caps, and is atomic
(tmp + replace); nothing here ever raises.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, List, Optional

try:
    from .wake import DEFAULT_PHRASE as _DEFAULT_PHRASE
except Exception:  # pragma: no cover - the store must stand alone
    _DEFAULT_PHRASE = "hey qubit"

WAKE_WORDS_FILENAME = "wake_words.json"

#: The built-in list, derived from the wake matcher's phrase (one source).
DEFAULT_TRIGGERS = (_DEFAULT_PHRASE,)

#: Safety caps, mirroring the upstream limits (count + per-trigger length).
MAX_WORDS = 16
MAX_LENGTH = 64


def state_dir() -> Path:
    """Canonical Qubit state dir, with a fail-soft default."""
    try:
        from ..config import canonical_state_dir
        return Path(canonical_state_dir())
    except Exception:
        return Path(os.path.expanduser("~/.local/state/qubit-voicebridge"))


def wake_words_path() -> Path:
    return state_dir() / WAKE_WORDS_FILENAME


def _strip_edge_punct(token: str) -> str:
    """Trim leading/trailing Unicode punctuation/symbol characters.

    Mirrors the upstream ``\\p{P}\\p{S}`` edge strip: only the token edges are
    touched, so inner punctuation (``don't``, ``hey.qubit``) survives.
    """
    start = 0
    end = len(token)
    while start < end and unicodedata.category(token[start])[0] in ("P", "S"):
        start += 1
    while end > start and unicodedata.category(token[end - 1])[0] in ("P", "S"):
        end -= 1
    return token[start:end]


def normalize_trigger(value: Any) -> str:
    """Normalize one trigger phrase; ``""`` for anything worthless.

    Lowercased, whitespace-collapsed to single spaces, edge punctuation /
    symbols stripped per token, empty tokens dropped. Never raises.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ""
    tokens: List[str] = []
    for token in value.lower().split():
        cleaned = _strip_edge_punct(token)
        if cleaned:
            tokens.append(cleaned)
    return " ".join(tokens)


def _sanitize(raw: Any) -> List[str]:
    """Normalize, drop empties/over-length, dedupe (order-preserving), cap.

    Accepts any input; a non-list degrades to ``[]``. Never raises. An empty
    result is returned as-is — the default fallback lives in :func:`load_triggers`
    so the persisted list and the effective list stay distinguishable.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    seen = set()
    for item in raw:
        word = normalize_trigger(item)
        if not word or len(word) > MAX_LENGTH or word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= MAX_WORDS:
            break
    return out


def add_trigger(triggers: Any, word: Any) -> List[str]:
    """Pure helper: return ``triggers`` plus ``word`` (normalized), or an
    unchanged sanitized copy when it is empty / over-length / a duplicate /
    beyond :data:`MAX_WORDS`. Never raises."""
    base = _sanitize(triggers)
    clean = normalize_trigger(word)
    if not clean or len(clean) > MAX_LENGTH or clean in base:
        return base
    if len(base) >= MAX_WORDS:
        return base
    return base + [clean]


def remove_trigger(triggers: Any, word: Any) -> List[str]:
    """Pure helper: return ``triggers`` without the normalized ``word``.
    Never raises."""
    base = _sanitize(triggers)
    clean = normalize_trigger(word)
    if not clean:
        return base
    return [t for t in base if t != clean]


def load_triggers(path: Any = None) -> List[str]:
    """Read the persisted list; missing/corrupt/empty -> defaults.

    Never raises: any failure (unreadable file, bad JSON, wrong shape, a list
    that sanitizes to nothing) fails closed to :data:`DEFAULT_TRIGGERS`.
    """
    try:
        raw = Path(path) if path is not None else wake_words_path()
        data = json.loads(raw.read_text(encoding="utf-8"))
        triggers = data.get("triggers") if isinstance(data, dict) else None
        cleaned = _sanitize(triggers)
        return cleaned or list(DEFAULT_TRIGGERS)
    except Exception:
        return list(DEFAULT_TRIGGERS)


def active_triggers(path: Any = None) -> List[str]:
    """The list the matcher should use (persisted, else defaults)."""
    return load_triggers(path)


def save_triggers(triggers: Any, path: Any = None) -> List[str]:
    """Normalize + dedupe + cap and atomically persist; returns what was saved.

    Writes ``{"triggers": [...], "updatedAtMs": ...}`` via tmp + replace so a
    crash can never leave a partial file. An empty sanitized list is written
    as-is (the effective defaults come back on the next read). Never raises;
    returns the sanitized list regardless of whether the write succeeded.
    """
    cleaned = _sanitize(triggers)
    try:
        target = Path(path) if path is not None else wake_words_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"triggers": cleaned, "updatedAtMs": int(time.time() * 1000)},
            indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                                   prefix=".wake-words-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, target)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass
    return cleaned


__all__ = [
    "WAKE_WORDS_FILENAME", "DEFAULT_TRIGGERS", "MAX_WORDS", "MAX_LENGTH",
    "state_dir", "wake_words_path", "normalize_trigger",
    "add_trigger", "remove_trigger", "load_triggers", "active_triggers",
    "save_triggers",
]
