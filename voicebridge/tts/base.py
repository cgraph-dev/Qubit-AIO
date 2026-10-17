"""TTS provider interface."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class TTSUnavailable(RuntimeError):
    pass


# Pre-compiled patterns for speakable_text() (ported from Starnet
# ``frontend/app/voice.js:speakable`` — the enforcement layer behind its
# VOICE_MODE_RULES prompt ask; the prompt alone cannot guarantee the model
# avoids markdown/URLs, so the spoken line is sanitized before synthesis).
_FORK_MARKER_RE = re.compile(r"\b(?:FORK|TASK_QUESTION)\s*:[^\n]*",
                             re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
_BARE_URL_RE = re.compile(r"https?://\S+")
_UNIX_PATH_RE = re.compile(r"(^|\s)[~.]?[/\\][\w./\\-]+")
_WIN_PATH_RE = re.compile(r"(^|\s)[A-Za-z]:[/\\][\w./\\-]+")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_STAR_RE = re.compile(r"\*([^*]+)\*")
_ITALIC_UNDER_RE = re.compile(r"_([^_]+)_")
_EMOJI_ARROW_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF]",
)


def speakable_text(text: str) -> str:
    """Strip what TTS would otherwise read LITERALLY (markdown, emoji,
    URLs/paths, choice markers).

    Ported from Starnet ``frontend/app/voice.js:speakable()`` so chat
    replies (which are markdown by construction) do not reach the
    synthesizer as ``"asterisk asterisk"`` / spelled-out URLs. Pure and
    cheap; safe to run on every spoken line.

    Deliberately NOT ported: Starnet's ``ULTRON -> Ultron`` all-caps rule
    (its locked station identity) and its 1200-char ``splitForTts``
    chunker (Qubit caps at 4000 in the caller instead).
    """
    s = str(text or "")
    s = _FORK_MARKER_RE.sub(" ", s)
    s = _CODE_FENCE_RE.sub(" ", s)
    s = _INLINE_CODE_RE.sub(r"\1", s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _BARE_URL_RE.sub("a link", s)
    s = _UNIX_PATH_RE.sub(r"\1that file", s)
    s = _WIN_PATH_RE.sub(r"\1that file", s)
    s = _MD_HEADER_RE.sub("", s)
    s = _BULLET_RE.sub("", s)
    s = _NUMBERED_RE.sub("", s)
    s = _BOLD_RE.sub(r"\1", s)
    s = _ITALIC_STAR_RE.sub(r"\1", s)
    s = _ITALIC_UNDER_RE.sub(r"\1", s)
    s = _EMOJI_ARROW_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def load(self) -> None:
        """Preload models/connections."""

    @abstractmethod
    def synthesize(self, text: str, out_path: Path | str) -> Path:
        """Render text to a WAV file (16/22.05/24 kHz mono) and return its path."""

    def speak(self, text: str, playback, out_dir: Optional[Path] = None,
              name: str = "") -> bool:
        """Synthesize and play. Returns False if playback was interrupted."""
        path = self.synthesize(text, out_dir and (out_dir / f"tts-{abs(hash(text))%10**8}.wav"))
        return playback.play(path, name=name or "tts")

    def available(self) -> bool:
        return True

    def unload(self) -> None:
        pass
