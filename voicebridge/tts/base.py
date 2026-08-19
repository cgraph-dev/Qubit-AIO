"""TTS provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class TTSUnavailable(RuntimeError):
    pass


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
