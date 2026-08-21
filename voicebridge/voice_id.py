"""Speaker verification (voice-ID) via sherpa-onnx — "is this ME?" gating.

Enrolls one speaker (the user) from a few seconds of their voice and answers
`verify(samples) -> (accepted, score)` in ~50ms on CPU. Used by the bridge to
gate barge-in: only the ENROLLED voice can interrupt the assistant (TV,
roommates, videos can't). Also the future foundation for hands-free mode.

Model: <model_dir>/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx (26 MB, CPU)
       from the k2-fsa/sherpa-onnx `speaker-recongition-models` release.

Chosen over wespeaker_en_voxceleb_resnet34 after measuring BOTH on the release
test clips (2026-08-21): resnet34 scores overlap (self 0.93-0.98 vs cross
0.79-0.85 — unusable), eres2net separates cleanly (self 0.82-0.94 vs cross
0.22-0.36). Threshold 0.75 sits in the middle with margin on both sides.

Enrollments are persisted as .npy embeddings under
<state_dir>/speakers/<name>.npy (source WAV kept alongside for audit).
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import List, Optional

import numpy as np

from .logging_setup import get_logger, log_event

log = get_logger()

MODEL_NAME = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/" + MODEL_NAME)


class SpeakerUnavailable(RuntimeError):
    pass


class SpeakerVerifier:
    def __init__(self, model_dir: Path, state_dir: Path,
                 name: str = "user", threshold: float = 0.65) -> None:
        self.model_path = Path(model_dir) / MODEL_NAME
        self.state_dir = Path(state_dir) / "speakers"
        self.name = name
        self.threshold = float(threshold)
        self._extractor = None
        self._manager = None

    # ------------------------------------------------------------- lifecycle
    def load(self) -> None:
        if not self.model_path.exists():
            raise SpeakerUnavailable(
                f"speaker model missing: {self.model_path}\n"
                f"download: {MODEL_URL}")
        try:
            import sherpa_onnx as so
        except Exception as exc:  # pragma: no cover
            raise SpeakerUnavailable(f"sherpa-onnx unavailable: {exc}") from exc
        self._extractor = so.SpeakerEmbeddingExtractor(
            so.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_path), num_threads=2,
                provider="cpu", debug=False))
        self._manager = so.SpeakerEmbeddingManager(self._extractor.dim)
        # replay persisted enrollments
        self.state_dir.mkdir(parents=True, exist_ok=True)
        loaded = 0
        for npy in sorted(self.state_dir.glob("*.npy")):
            try:
                emb = np.load(npy).astype(np.float32)
                spk = npy.stem
                self._manager.add(spk, emb)
                loaded += 1
            except Exception as exc:
                log_event("speaker.enroll.load_failed", name=npy.stem,
                          error=str(exc)[:120])
        if loaded:
            log_event("speaker.verifier", model=MODEL_NAME, speakers=loaded,
                      dim=self._extractor.dim)
        else:
            log_event("speaker.verifier", model=MODEL_NAME, speakers=0,
                      hint="run `voicebridge enroll` to enroll your voice")

    @property
    def ready(self) -> bool:
        return self._extractor is not None and self._manager is not None \
            and self._manager.num_speakers > 0

    @property
    def num_speakers(self) -> int:
        return self._manager.num_speakers if self._manager else 0

    # ------------------------------------------------------------- embeddings
    def embedding_from_wav(self, wav_path: Path | str) -> np.ndarray:
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) == 0:
            raise SpeakerUnavailable(f"empty wav: {wav_path}")
        return self.embedding_from_samples(samples, sr)

    def embedding_from_samples(self, samples: np.ndarray,
                               sample_rate: int = 16000) -> np.ndarray:
        if self._extractor is None:
            self.load()
        if len(samples) < int(1.0 * sample_rate):
            raise SpeakerUnavailable(
                f"too short for speaker ID ({len(samples)/sample_rate:.1f}s; need >=1s)")
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=int(sample_rate),
                               waveform=samples.astype(np.float32))
        return np.array(self._extractor.compute(stream), dtype=np.float32)

    def _rolling_embeddings(self, samples: np.ndarray, sr: int,
                            window_s: float = 2.0, step_s: float = 1.0) -> List[np.ndarray]:
        """Slide a window over the audio and embed each chunk (more robust)."""
        win = int(window_s * sr)
        step = int(step_s * sr)
        embs = []
        for i in range(0, len(samples) - win + 1, step):
            try:
                embs.append(self.embedding_from_samples(samples[i:i + win], sr))
            except SpeakerUnavailable:
                continue
        return embs

    # --------------------------------------------------------------- enroll
    def enroll(self, wav_path: Path | str) -> float:
        """Enroll the speaker from a WAV (>=3s recommended). Returns self-score."""
        self.load()
        wav_path = Path(wav_path)
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        embs = self._rolling_embeddings(samples, sr, window_s=1.5, step_s=0.75)
        try:
            full = self.embedding_from_samples(samples, sr)  # whole-clip embedding
            embs.append(full)
        except SpeakerUnavailable:
            pass
        if len(embs) < 2:
            raise SpeakerUnavailable(
                f"enrollment too short: got {len(samples)/sr:.1f}s of audio, "
                "need >=2s of voice (>=3s recommended)")
        emb = np.mean(embs, axis=0).astype(np.float32)
        self._manager.add(self.name, emb)
        # persist
        self.state_dir.mkdir(parents=True, exist_ok=True)
        np.save(self.state_dir / f"{self.name}.npy", emb)
        # keep a copy of the source wav for audit/re-enrollment
        dst = self.state_dir / f"{self.name}.wav"
        dst.write_bytes(wav_path.read_bytes())
        self_score = float(self._manager.score(self.name, emb))
        log_event("speaker.enrolled", name=self.name, self_score=round(self_score, 3),
                  seconds=round(len(samples) / sr, 1), embeddings=len(embs))
        return self_score

    # --------------------------------------------------------------- verify
    def verify(self, samples: np.ndarray, sample_rate: int = 16000,
               name: Optional[str] = None) -> tuple[bool, float]:
        """(accepted, cosine_score) for `samples` vs the enrolled speaker."""
        if not self.ready:
            return False, 0.0
        emb = self.embedding_from_samples(samples, sample_rate)
        n = name or self.name
        score = float(self._manager.score(n, emb))
        accepted = self._manager.verify(n, emb, self.threshold)
        return bool(accepted), score

    def verify_wav(self, wav_path: Path | str,
                   name: Optional[str] = None) -> tuple[bool, float]:
        """(accepted, cosine_score) for a WAV file vs the enrolled speaker."""
        if not self.ready:
            return False, 0.0
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return self.verify(samples, sr, name)
