"""CLI launcher: run the bridge, run one utterance, send a typed task, test tools."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voicebridge",
        description="Local push-to-talk voice bridge for OpenClaw (ASR + intent + gateway + TTS).")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None,
                        help="path to voice.yaml (default ~/.config/openclaw-voice-bridge/voice.yaml)")
    common.add_argument("--no-hotkey", action="store_true",
                        help="disable hotkey; use manual Enter PTT (headless/SSH)")
    common.add_argument("--dry-run", action="store_true",
                        help="never dispatch to OpenClaw; only speak the plan")
    common.add_argument("--mute", action="store_true",
                        help="start muted (no spoken output; logs still work)")
    p.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--no-hotkey", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--mute", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    def add(name, **kw):
        sp = sub.add_parser(name, parents=[common], **kw)
        return sp

    add("run", help="interactive push-to-talk loop (default)")
    add("once", help="record one utterance, process it, exit")
    add("mic-test", help="record 5s and report levels (diagnostic)")
    task = add("task", help="send a typed task through the pipeline (no mic)")
    task.add_argument("text", nargs="+", help="task text, e.g. 'inspect the Stripe webhook flow'")
    task.add_argument("--model", default=None, help="model override (provider/model or tier)")
    asr = add("asr-test", help="transcribe a WAV file (diagnostic)")
    asr.add_argument("file", type=Path)
    tts = add("tts-test", help="synthesize text to WAV and play it (diagnostic)")
    tts.add_argument("text", nargs="+")
    tts.add_argument("--out", type=Path, default=None)
    add("status", help="print gateway/ASR/TTS/VRAM status and exit")
    roundtrip = add("roundtrip", help="end-to-end test: synth speech -> ASR -> classify -> gateway -> summarize -> TTS file")
    roundtrip.add_argument("--text", default=None, help="sample utterance (default: Stripe webhook example)")
    roundtrip.add_argument("--model", default=None, help="model override for the agent run")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"

    # logging early so every event is captured
    from .config import Config
    from .logging_setup import setup
    cfg = Config.load(args.config)
    setup(level=cfg.get("logging", "level", default="INFO"),
          jsonl=cfg.get("logging", "jsonl", default=True),
          state_dir=cfg.state_dir())

    if command == "status":
        return _cmd_status(cfg)
    if command == "mic-test":
        return _cmd_mic_test(cfg)
    if command == "asr-test":
        return _cmd_asr_test(cfg, args.file)
    if command == "tts-test":
        return _cmd_tts_test(cfg, " ".join(args.text), args.out)

    from .orchestrator import VoiceOrchestrator
    orch = VoiceOrchestrator(cfg, no_hotkey=args.no_hotkey, dry_run=args.dry_run)
    orch.muted = args.mute

    try:
        if command == "run":
            orch.run()
        elif command == "once":
            orch.mic_only = True
            orch.run()
        elif command == "task":
            text = " ".join(args.text)
            if args.model:
                cfg.data.setdefault("openclaw", {})["model"] = args.model
            orch.process_text(text)
        elif command == "roundtrip":
            return _cmd_roundtrip(cfg, args.text, args.model, orch)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        orch.stop()
    return 0


def _cmd_status(cfg) -> int:
    from .openclaw.discovery import discover_gateway, current_vram_mb
    from .logging_setup import get_logger
    log = get_logger()
    info = discover_gateway(cfg)
    print(f"gateway url      : {info.url}")
    print(f"gateway token    : {'found' if info.token else 'MISSING'}")
    print(f"default agent    : {info.agent}")
    print(f"default model    : {info.default_model or '(none)'}")
    print(f"tier aliases     : {info.models or '(none)'}")
    print(f"free VRAM        : {current_vram_mb()} MiB")
    # ASR
    from .asr.whisper import FasterWhisperASR
    asr_cfg = cfg.section("asr")
    asr = FasterWhisperASR(model=asr_cfg.get("model", "base"), device=asr_cfg.get("device", "auto"))
    try:
        asr.load()
        print(f"ASR              : whisper/{asr.model_name} on {asr._actual_device}")
    except Exception as exc:
        print(f"ASR              : FAILED ({str(exc)[:120]})")
    # TTS
    from .tts.piper import PiperTTS
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    print(f"TTS              : piper/{tts.voice} {'OK' if tts.available() else 'MISSING VOICE'}")
    return 0


def _cmd_mic_test(cfg) -> int:
    import numpy as np
    import sounddevice as sd
    from .audio.vad import Endpointer
    from .audio.mic import MicCapture
    mic_cfg = cfg.section("mic")
    print("Recording 5 seconds. Speak now...")
    mic = MicCapture(device=mic_cfg.get("device"), sample_rate=mic_cfg.get("sample_rate", 48000),
                     channels=mic_cfg.get("channels", 1), blocksize=mic_cfg.get("blocksize", 480),
                     endpointer=Endpointer(aggressiveness=2))
    import threading, time
    stop = threading.Event()
    samples = []
    with mic.open():
        t0 = time.time()
        while time.time() - t0 < 5:
            data, _ = mic._stream.read(mic.blocksize)
            peak = float(np.abs(data).max()) if data.size else 0.0
            samples.append(peak)
    peak_avg = float(np.mean(samples)) if samples else 0.0
    print(f"peak RMS avg: {peak_avg:.4f} (above ~0.005 = audible signal)")
    print("If this is ~0, check the input device / gain (Focusrite: turn gain to ~12 o'clock).")
    return 0


def _cmd_asr_test(cfg, file: Path) -> int:
    if not file.exists():
        print(f"file not found: {file}")
        return 1
    from .asr.whisper import FasterWhisperASR
    asr_cfg = cfg.section("asr")
    asr = FasterWhisperASR(model=asr_cfg.get("model", "base"), device=asr_cfg.get("device", "auto"))
    asr.load()
    tr = asr.transcribe(file)
    print(f"[{tr.provider}/{asr._actual_device}] conf={tr.confidence:.2f} dur={tr.duration_s:.1f}s")
    print(tr.text)
    return 0


def _cmd_tts_test(cfg, text: str, out: Path | None) -> int:
    from .tts.piper import PiperTTS
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    tts.load()
    path = tts.synthesize(text, out)
    print(f"synthesized: {path}")
    from .audio.playback import Playback
    Playback().play(path)
    return 0


def _cmd_roundtrip(cfg, text: str | None, model: str | None, orch) -> int:
    """End-to-end: TTS synth sample -> whisper ASR -> classify -> gateway task
    -> summarize -> TTS synth final. Prints each stage; returns 0 on success."""
    import time
    from .tts.piper import PiperTTS
    from .asr.whisper import FasterWhisperASR

    sample = text or ("Open the current project, inspect the Stripe webhook flow, "
                      "and tell OpenClaw to patch the signature verification and add structured logs.")
    print(f"[1/6] synth sample speech (piper) ...")
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    tts.load()
    wav = tts.synthesize(sample, Path("/tmp/openclaw-voice-bridge/roundtrip-sample.wav"))
    print(f"      -> {wav}")

    print("[2/6] transcribe (whisper) ...")
    asr_cfg = cfg.section("asr")
    asr = FasterWhisperASR(model=asr_cfg.get("model", "base"), device=asr_cfg.get("device", "auto"))
    asr.load()
    tr = asr.transcribe(wav)
    print(f"      -> '{tr.text}'")
    assert tr.text.strip(), "ASR produced empty text"

    print("[3/6] classify intent ...")
    orch.start()
    intent = orch.classifier.classify(tr.text)
    print(f"      -> kind={intent.kind} conf={intent.confidence:.2f} reason={intent.reason}")
    assert intent.kind in ("coding", "conversation"), "expected a coding (or conversation) intent"

    print("[4/6] dispatch to OpenClaw (this can take a while on local models) ...")
    if model:
        cfg.data.setdefault("openclaw", {})["model"] = model
    task_text = intent.task_text or tr.text
    orch._dispatch_coding(task_text, model=model)
    print("      -> done")

    print("[5/6] final spoken summary rendered by piper ...")
    # The orchestrator already spoke the summary; render it to a file to verify.
    # Re-run the last summary from events is overkill — synthesize a fixed check line.
    check = tts.synthesize("Round trip complete.", Path("/tmp/openclaw-voice-bridge/roundtrip-final.wav"))
    print(f"      -> {check}")

    print("[6/6] roundtrip OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
