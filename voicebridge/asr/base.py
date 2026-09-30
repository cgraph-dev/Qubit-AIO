"""ASR provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ASRUnavailable(RuntimeError):
    """Raised when an ASR provider cannot run (no server, no model, OOM)."""


@dataclass
class Transcript:
    text: str
    language: Optional[str] = None
    confidence: float = 0.0          # provider estimate, 0-1 (0 = unknown)
    duration_s: float = 0.0
    segments: List["Segment"] = field(default_factory=list)
    provider: str = ""

    @property
    def empty(self) -> bool:
        return not self.text.strip()


@dataclass
class Segment:
    start: float
    end: float
    text: str
    confidence: Optional[float] = None


class ASRProvider(ABC):
    name: str = "base"

    @abstractmethod
    def load(self) -> None:
        """Preload models/servers. Called once at startup (lazy is fine)."""

    @abstractmethod
    def transcribe(self, audio_path: Path | str) -> Transcript:
        """Transcribe an audio file (16 kHz mono WAV)."""

    def available(self) -> bool:
        """True if this provider can currently serve requests."""
        return True

    def unload(self) -> None:
        """Release GPU memory. Best-effort."""
