"""Speaker verification (voice-ID) via sherpa-onnx — "is this ME?" gating.

Enrolls one speaker (the user) from a few seconds of their voice and answers
`verify(samples) -> (accepted, score)` in ~50ms on CPU. Used by the bridge to
gate barge-in (only the ENROLLED voice can interrupt the assistant) and, via
:func:`check_command_audio`, to gate hands-free wake/confirmation actions when
an owner profile is enrolled (TV, roommates, videos can't arm or confirm).

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
from typing import Any, List, Optional

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


def load_verifier(cfg: Any, *, default_enabled: bool = True) -> Optional[SpeakerVerifier]:
    """Build and load the configured speaker verifier, or ``None``.

    This is the ONE place a runtime caller (message bridge or desktop helper)
    decides whether voice-ID is active. It fails OPEN: disabled config, a
    missing model, missing enrollment or any load error logs a typed event and
    returns ``None`` — which callers treat as "any speaker", the historical
    behaviour. A truthy, ready verifier means only the enrolled voice passes.

    Never raises.
    """
    try:
        cfg_any = cfg
        enabled = cfg_any.get("speaker_verify", "enabled", default=default_enabled)
    except Exception:
        enabled = default_enabled
    if not enabled:
        return None
    try:
        name = str(cfg.get("speaker_verify", "name", default="user") or "user")
        threshold = float(cfg.get("speaker_verify", "threshold", default=0.65))
        verifier = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(),
                                   name=name, threshold=threshold)
        verifier.load()
        if verifier.ready:
            log_event("speaker.verify.active", model=MODEL_NAME,
                      name=name, threshold=threshold,
                      speakers=int(verifier.num_speakers))
            return verifier
        log_event("speaker.verify.no_enrollment",
                  hint="run `qubit voice enroll` to enrol your voice")
    except Exception as exc:  # noqa: BLE001 - voice-ID is fail-open
        log_event("speaker.verify.unavailable", error=str(exc)[:160])
    return None


#: Identity policy for ACTIONS (wake activation + spoken confirmations).
#: ``off``      -- owner explicitly disabled voice-ID (or command gating).
#: ``inactive`` -- enabled, but no enrolled voice/model is available, so no
#:                 identity profile exists (historical "any voice" behaviour).
#: ``required`` -- an owner profile exists; only the enrolled voice may act.
POLICY_OFF = "off"
POLICY_INACTIVE = "inactive"
POLICY_REQUIRED = "required"

REASON_POLICY_OFF = "policy_off"
REASON_NO_PROFILE = "no_profile"
REASON_VERIFIED = "verified"
REASON_UNVERIFIED = "unverified"
REASON_INSUFFICIENT_AUDIO = "insufficient_audio"
REASON_VERIFY_ERROR = "verify_error"


def _cfg_value(cfg: Any, section: str, key: str, default: Any) -> Any:
    try:
        return cfg.get(section, key, default=default)
    except Exception:
        return default


def _enrolled_profile_present(cfg: Any,
                              verifier: Optional[SpeakerVerifier]) -> bool:
    """True when an enrollment file exists for the configured owner name.

    Used to distinguish "no profile enrolled" (normal wake) from "a profile is
    enrolled but the verifier failed to load" (must fail closed rather than
    silently downgrade to any-voice). Never raises.
    """
    if verifier is not None and getattr(verifier, "ready", False):
        return True
    try:
        name = str(_cfg_value(cfg, "speaker_verify", "name", "user") or "user")
        return (Path(cfg.state_dir()) / "speakers" / f"{name}.npy").is_file()
    except Exception:
        return False


def command_identity_policy(cfg: Any,
                            verifier: Optional[SpeakerVerifier]) -> str:
    """Return the voice-ID policy for wake/confirmation ACTIONS.

    Unlike barge-in (which is deliberately fail-open and only active when a
    verifier is ready), command gating distinguishes three states so a
    missing enrollment is not confused with an explicit opt-out:

    * ``speaker_verify.enabled: false`` -> :data:`POLICY_OFF` (explicit opt-out).
    * ``speaker_verify.require_commands: false`` -> :data:`POLICY_OFF`
      (owner wants barge-in gating only).
    * enabled + ``require_commands`` (default) + no enrolled profile ->
      :data:`POLICY_INACTIVE` (normal behaviour).
    * enabled + ``require_commands`` + an enrolled profile or ready verifier
      -> :data:`POLICY_REQUIRED` (a stored enrollment whose model failed to
      load is still REQUIRED, so verification fails closed instead of
      downgrading).
    """
    if not bool(_cfg_value(cfg, "speaker_verify", "enabled", True)):
        return POLICY_OFF
    if not bool(_cfg_value(cfg, "speaker_verify", "require_commands", True)):
        return POLICY_OFF
    if not _enrolled_profile_present(cfg, verifier):
        return POLICY_INACTIVE
    return POLICY_REQUIRED


def check_command_audio(cfg: Any, verifier: Optional[SpeakerVerifier],
                        pcm: bytes, sample_rate: int = 16000, *,
                        via: str = "") -> tuple[bool, str]:
    """``(allowed, reason)`` for a wake/confirmation utterance.

    Enforces :func:`command_identity_policy`. When a profile is REQUIRED the
    check fails **closed**: an unverified speaker, too-short audio, or any
    embedding error refuses the action with a typed reason instead of silently
    downgrading to the old any-voice behaviour. No verifier/enrollment or an
    explicit opt-out keeps the historical allow path.

    Never raises.
    """
    policy = command_identity_policy(cfg, verifier)
    if policy == POLICY_OFF:
        return True, REASON_POLICY_OFF
    if policy == POLICY_INACTIVE:
        return True, REASON_NO_PROFILE
    try:
        samples = (np.frombuffer(bytes(pcm), dtype="<i2").astype(np.float32)
                   / 32768.0)
        accepted, score = verifier.verify(samples, int(sample_rate))
    except SpeakerUnavailable as exc:
        log_event("speaker.command_gate", accepted=False,
                  reason=REASON_INSUFFICIENT_AUDIO, via=via or None,
                  error=str(exc)[:120])
        return False, REASON_INSUFFICIENT_AUDIO
    except Exception as exc:  # noqa: BLE001 - fail CLOSED for actions
        log_event("speaker.command_gate", accepted=False,
                  reason=REASON_VERIFY_ERROR, via=via or None,
                  error=str(exc)[:120])
        return False, REASON_VERIFY_ERROR
    ok = bool(accepted)
    log_event("speaker.command_gate", accepted=ok,
              score=round(float(score), 3), via=via or None,
              reason=REASON_VERIFIED if ok else REASON_UNVERIFIED)
    return ok, (REASON_VERIFIED if ok else REASON_UNVERIFIED)


def allows_pcm(verifier: Optional[SpeakerVerifier], pcm: bytes,
               sample_rate: int = 16000, *, min_seconds: float = 1.0,
               via: str = "") -> bool:
    """True when ``pcm`` may act as the owner (fail-open).

    Shared by the message bridge's barge monitor and the desktop helper's
    wake-phrase barge gate, so both enforce the SAME rule. No verifier, no
    enrollment, a too-short sample (under ``min_seconds``) or any embedding
    error returns True: voice-ID must never wedge normal speech. Only a
    successful embedding that scores below the threshold returns False.
    """
    if verifier is None or not getattr(verifier, "ready", False):
        return True
    try:
        n = max(0, int(min_seconds * max(1, int(sample_rate))))
        if len(pcm) // 2 < n:
            return True
        samples = (np.frombuffer(bytes(pcm), dtype="<i2").astype(np.float32)
                   / 32768.0)
        accepted, score = verifier.verify(samples, int(sample_rate))
        log_event("speaker.verify", accepted=bool(accepted),
                  score=round(float(score), 3), via=via or None)
        return bool(accepted)
    except Exception as exc:  # noqa: BLE001 - fail open, never block speech
        log_event("speaker.verify.error", error=str(exc)[:120],
                  via=via or None)
        return True
