"""Kokoro-82M TTS via sherpa-onnx — optional quality provider.

Best-sounding CPU voice available locally (MOS ~4.4), but on this box int8
synthesis is ~1x realtime (1.1s for a short reply) vs piper's ~0.25s. Use for
quality mode (`tts.provider: kokoro`); keep piper for latency-first default.

Model: <model_dir>/kokoro-int8-en-v0_19/
  (model.int8.onnx + tokens.txt + voices.bin + espeak-ng-data/)
Voices: sid 0..num_speakers-1 (v0.19 EN has 11). Default sid 0.
"""
from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from ..logging_setup import get_logger, log_event
from .base import TTSProvider, TTSUnavailable

log = get_logger()


class KokoroTTS(TTSProvider):
    name = "kokoro"

    def __init__(self, model_dir: Path, voice_sid: int = 0,
                 sample_rate: int = 24000) -> None:
        self.model_dir = Path(model_dir)
        self.voice_sid = int(voice_sid)
        self.sample_rate = sample_rate
        self._tts = None
        self._lock = threading.Lock()

    def _model_paths(self):
        base = self.model_dir / "kokoro-int8-en-v0_19"
        return {
            "model": base / "model.int8.onnx",
            "tokens": base / "tokens.txt",
            "voices": base / "voices.bin",
            "data": base / "espeak-ng-data",
        }

    def load(self) -> None:
        with self._lock:
            if self._tts is not None:
                return
            import sherpa_onnx as so
            p = self._model_paths()
            missing = [str(v) for v in p.values() if not v.exists()]
            if missing:
                raise TTSUnavailable(
                    f"kokoro model missing: {missing}\n"
                    "download kokoro-int8-en-v0_19.tar.bz2 from the "
                    "k2-fsa/sherpa-onnx tts-models release into <model_dir>")
            try:
                self._tts = so.OfflineTts(so.OfflineTtsConfig(
                    model=so.OfflineTtsModelConfig(
                        provider="cpu", num_threads=4, debug=False,
                        kokoro=so.OfflineTtsKokoroModelConfig(
                            model=str(p["model"]),
                            tokens=str(p["tokens"]),
                            voices=str(p["voices"]),
                            data_dir=str(p["data"]),
                            dict_dir="",
                            lexicon="",
                            length_scale=1.0,
                        ),
                    ),
                    max_num_sentences=1,
                ))
            except Exception as exc:  # pragma: no cover
                raise TTSUnavailable(f"kokoro load failed: {exc}") from exc
            if self.voice_sid >= self._tts.num_speakers:
                log_event("tts.kokoro.sid", sid=self.voice_sid,
                          num_speakers=self._tts.num_speakers, clamped=True)
                self.voice_sid = 0
            log_event("tts.load", provider=self.name,
                      voice=f"sid-{self.voice_sid}",
                      speakers=self._tts.num_speakers)

    def synthesize(self, text: str, out_path: Optional[Path | str] = None) -> Path:
        self.load()
        out_path = Path(out_path) if out_path else Path(
            f"/tmp/qubit-voicebridge/tts-{abs(hash(text))%10**8}.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            audio = self._tts.generate(text, sid=self.voice_sid, speed=1.0)
        sr = audio.sample_rate
        samples = np.asarray(audio.samples, dtype=np.float32)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
        log_event("tts.synthesize", provider=self.name, chars=len(text),
                  path=str(out_path), sid=self.voice_sid)
        return out_path

    def available(self) -> bool:
        try:
            self._model_paths()
            return True
        except Exception:
            return False
