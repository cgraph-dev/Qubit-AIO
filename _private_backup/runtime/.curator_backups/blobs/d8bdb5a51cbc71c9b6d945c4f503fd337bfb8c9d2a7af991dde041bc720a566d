#!/usr/bin/env python3
"""Benchmark local voice stack: piper TTS (CPU) -> WAV -> faster-whisper (GPU) transcription.

Edit MODELS / TEXT / ASR_MODEL / ASR_DEVICE at the top. On an RTX 3070 Ti 8GB,
expect: piper load ~0.8s, synth ~0.3s for a long sentence; whisper base fp16
load ~0.6s, transcribe ~0.05s for ~7s audio (~180x realtime).

For GPU whisper on this box, ctranslate2 needs cuBLAS from Ollama's bundle:
    export LD_LIBRARY_PATH=/usr/local/lib/ollama/cuda_v12
before running (or the script sets it for you if that dir exists).

Usage: python3 bench_voice.py
Needs: faster-whisper, piper-tts (+ a piper voice downloaded, see SKILL.md)
"""
import os
import sys
import time
import wave

MODELS = os.path.expanduser("~/.local/share/openclaw-voice-bridge/models")
VOICE = os.path.join(MODELS, "en_US-amy-medium.onnx")
TEXT = ("Open the current project, inspect the stripe webhook flow, "
        "and tell Qubit to patch the signature verification.")
ASR_MODEL = "base"
ASR_DEVICE = "cuda"

# cuBLAS bootstrap: Ollama bundles cu12 libs that are otherwise not on the path.
_CU12 = "/usr/local/lib/ollama/cuda_v12"
if ASR_DEVICE == "cuda" and os.path.isdir(_CU12):
    os.environ["LD_LIBRARY_PATH"] = _CU12 + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")


def main() -> None:
    t0 = time.time()
    from piper import PiperVoice
    voice = PiperVoice.load(VOICE)
    t1 = time.time()
    print(f"piper load: {t1-t0:.2f}s")

    out_path = "/tmp/bench_sample.wav"
    t2 = time.time()
    with wave.open(out_path, "wb") as wav_file:
        voice.synthesize_wav(TEXT, wav_file)
    t3 = time.time()
    print(f"piper synth '{TEXT[:40]}...': {t3-t2:.2f}s -> {out_path}")

    t4 = time.time()
    from faster_whisper import WhisperModel
    model = WhisperModel(ASR_MODEL, device=ASR_DEVICE, compute_type="float16")
    t5 = time.time()
    print(f"whisper {ASR_MODEL} load ({ASR_DEVICE}): {t5-t4:.2f}s")
    segments, info = model.transcribe(out_path, beam_size=1, language="en")
    text = "".join(s.text for s in segments)
    t6 = time.time()
    print(f"transcribe: {t6-t5:.2f}s (audio {info.duration:.1f}s) -> '{text.strip()}'")
    print(f"RTF: {(t6-t5)/info.duration:.2f} (lower is better; <1 = faster than realtime)")


if __name__ == "__main__":
    main()
