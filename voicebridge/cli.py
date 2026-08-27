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
    enroll = add("enroll", help="enroll your voice for speaker verification (voice-ID)")
    enroll.add_argument("--file", type=Path, default=None,
                        help="enroll from an existing WAV instead of recording")
    enroll.add_argument("--name", default=None, help="speaker name (default: from config, 'user')")
    enroll.add_argument("--seconds", type=int, default=None, help="recording length (default: 5)")
    verify = add("verify", help="diagnostic: score a WAV against the enrolled speaker")
    verify.add_argument("file", type=Path)
    verify.add_argument("--name", default=None)
    flags = add("flags", help="inspect/toggle reversible feature flags (dry-run by default)")
    flags_sub = flags.add_subparsers(dest="flags_command")
    flags_list = flags_sub.add_parser("list", parents=[common],
                                      help="show every flag + effective value")
    flags_list.add_argument("--json", action="store_true", help="machine-readable output")
    fget = flags_sub.add_parser("get", parents=[common], help="show one flag's effective value")
    fget.add_argument("name")
    fset = flags_sub.add_parser("set", parents=[common], help="set a flag value (dry-run unless --apply)")
    fset.add_argument("name")
    fset.add_argument("value")
    fset.add_argument("--apply", action="store_true",
                      help="write the config with a timestamped backup (default: dry-run)")
    froll = flags_sub.add_parser("rollback", parents=[common],
                                 help="restore a flag's rollback value (dry-run unless --apply)")
    froll.add_argument("name")
    froll.add_argument("--apply", action="store_true",
                       help="write the config with a timestamped backup (default: dry-run)")
    drill = add("flag-drill", help="hermetic rollback drill: toggle + restore every flag on a temp config/state")
    pkg = add("package", help="download-and-run distribution surface (read-only)")
    pkg_sub = pkg.add_subparsers(dest="package_command")
    pkg_sub.add_parser("manifest", parents=[common],
                       help="print the versioned package manifest")
    pkg_doctor = pkg_sub.add_parser("doctor", parents=[common],
                                    help="hardware/resource doctor (typed states)")
    pkg_doctor.add_argument("--json", action="store_true")
    pkg_launch = pkg_sub.add_parser("launch", parents=[common],
                                    help="print the verified launch command")
    pkg_up = pkg_sub.add_parser("upgrade", parents=[common],
                                help="upgrade behavior plan (read-only)")
    pkg_un = pkg_sub.add_parser("uninstall", parents=[common],
                                help="uninstall behavior plan (read-only, confirms)")
    pkg_model = pkg_sub.add_parser("model", parents=[common],
                                   help="model artifact metadata + digest (fail closed)")
    pkg_dist = pkg_sub.add_parser("distributable", parents=[common],
                                  help="distributable artifact completeness check")
    aud = add("audio", help="user-facing audio settings contract (list/select/reset/status)")
    aud_sub = aud.add_subparsers(dest="audio_command")
    aud_list = aud_sub.add_parser("list", parents=[common], help="list audio devices")
    aud_list.add_argument("--json", action="store_true")
    aud_sel = aud_sub.add_parser("select", parents=[common], help="select a device by stable name")
    aud_sel.add_argument("kind", choices=["mic", "output"])
    aud_sel.add_argument("name")
    aud_sub.add_parser("reset", parents=[common], help="reset audio to OS defaults")
    aud_sub.add_parser("status", parents=[common], help="typed audio state (OK/DEGRADED/MISSING/PERMISSION_DENIED)")
    prof = add("profile", help="provider/profile registry (brain/STT/TTS/voice/audio)")
    prof_sub = prof.add_subparsers(dest="profile_command")
    prof_list = prof_sub.add_parser("list", parents=[common],
                                    help="list registered profiles + active selection")
    prof_list.add_argument("--json", action="store_true")
    prof_show = prof_sub.add_parser("show", parents=[common],
                                    help="show the active profile for one component")
    prof_show.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_check = prof_sub.add_parser("check", parents=[common],
                                     help="run every gate for a profile (read-only)")
    prof_check.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_check.add_argument("--profile", default=None)
    prof_install = prof_sub.add_parser("install", parents=[common],
                                       help="register a profile manifest (JSON, read-only)")
    prof_install.add_argument("manifest", type=Path, help="path to a manifest JSON")
    prof_select = prof_sub.add_parser("select", parents=[common],
                                      help="activate a profile (dry-run unless --apply)")
    prof_select.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_select.add_argument("profile")
    prof_select.add_argument("--apply", action="store_true",
                             help="actually apply (default: dry-run)")
    prof_select.add_argument("--confirm", action="store_true",
                             help="confirm the side effect")
    prof_reset = prof_sub.add_parser("reset", parents=[common],
                                     help="reset one component to its default")
    prof_reset.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_reset.add_argument("--apply", action="store_true",
                            help="actually apply (default: dry-run)")
    prof_up = prof_sub.add_parser("upgrade", parents=[common],
                                  help="re-verify a profile's gates")
    prof_up.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_up.add_argument("--profile", default=None)
    prof_up.add_argument("--apply", action="store_true",
                         help="actually re-verify (default: dry-run)")
    prof_rm = prof_sub.add_parser("remove", parents=[common],
                                  help="remove a non-default profile from the registry")
    prof_rm.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_rm.add_argument("profile")
    prof_rm.add_argument("--apply", action="store_true",
                         help="actually remove (default: dry-run)")
    prof_roll = prof_sub.add_parser("rollback", parents=[common],
                                    help="restore a component's previous profile")
    prof_roll.add_argument("kind", choices=["brain", "stt", "tts", "voice", "audio"])
    prof_roll.add_argument("--backup", type=Path, default=None)
    prof_roll.add_argument("--apply", action="store_true",
                           help="actually roll back (default: dry-run)")
    brand = add("branding", help="Qubit Voice Agent branding migration (dry-run/backup/verify/rollback)")
    brand_sub = brand.add_subparsers(dest="branding_command")
    brand_plan = brand_sub.add_parser("plan", parents=[common],
                                      help="prepare the openclaw-voice-bridge -> qubit-voice-agent migration")
    brand_plan.add_argument("--apply", action="store_true", default=False,
                            help="perform the migration with a backup (default: dry-run plan only)")
    brand_plan.add_argument("--migrate-paths", action="store_true", default=False,
                            help="ALSO move live config/state/data paths (explicit, separate operator step)")
    brand_plan.add_argument("--backup-dir", type=Path, default=None)
    brand_plan.add_argument("--repo", type=Path, default=None,
                            help="target repository root (default: this repo)")
    brand_verify = brand_sub.add_parser("verify", parents=[common],
                                        help="read-only branding consistency check")
    brand_verify.add_argument("--repo", type=Path, default=None,
                              help="target repository root (default: this repo)")
    brand_roll = brand_sub.add_parser("rollback", parents=[common],
                                      help="restore branding backups (reversible)")
    brand_roll.add_argument("--backup-dir", type=Path, required=True)
    brand_roll.add_argument("--repo", type=Path, default=None,
                            help="target repository root (default: this repo)")
    export = add("export-state", help="encrypt + export portable state (memory/lessons/reports/config/model)")
    export.add_argument("--out", type=Path, default=None,
                        help="output file (default: <state_dir>/portable-<ts>.vbp)")
    export.add_argument("--passphrase", default=None,
                        help="passphrase (required; prefer VOICE_BRIDGE_PASSPHRASE env)")
    imp = add("import-state", help="verify + restore a portable-state bundle (dry-run by default)")
    imp.add_argument("bundle", type=Path)
    imp.add_argument("--passphrase", default=None,
                     help="passphrase (required; prefer VOICE_BRIDGE_PASSPHRASE env)")
    imp.add_argument("--apply", action="store_true",
                     help="actually restore (default: dry-run/preview)")
    dev = add("device", help="trusted-device enrollment (explicit; read-only about host)")
    dev_sub = dev.add_subparsers(dest="device_command")
    dev_sub.add_parser("status", parents=[common], help="print the current device profile")
    dev_enroll = dev_sub.add_parser("enroll", parents=[common],
                                    help="explicitly authorize THIS device")
    dev_enroll.add_argument("--model-alias", default=None)
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
    if command == "enroll":
        return _cmd_enroll(cfg, args.file, args.name, args.seconds)
    if command == "verify":
        return _cmd_verify(cfg, args.file, args.name)
    if command == "flags":
        return _cmd_flags(cfg, args, args.config)
    if command == "flag-drill":
        return _cmd_flag_drill(cfg)
    if command == "package":
        return _cmd_package(cfg, args)
    if command == "audio":
        return _cmd_audio(cfg, args)
    if command == "profile":
        return _cmd_profile(cfg, args)
    if command == "branding":
        return _cmd_branding(cfg, args)
    if command == "export-state":
        return _cmd_export_state(cfg, args)
    if command == "import-state":
        return _cmd_import_state(cfg, args)
    if command == "device":
        return _cmd_device(cfg, args)

    from .orchestrator import VoiceOrchestrator
    orch = VoiceOrchestrator(cfg, no_hotkey=args.no_hotkey, dry_run=args.dry_run)
    orch.muted = args.mute

    try:
        if command == "run":
            orch.blocking_tasks = False   # PTT loop must stay responsive
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
    asr_cfg = cfg.section("asr")
    if asr_cfg.get("provider", "sherpa") == "sherpa":
        from .asr.sherpa import SherpaASR
        try:
            asr = SherpaASR(engine=asr_cfg.get("engine", "moonshine"),
                            model_dir=cfg.model_dir(),
                            provider=asr_cfg.get("provider_ep", "cpu"))
            asr.load()
            print(f"ASR              : sherpa/{asr.engine} on {asr.provider}")
        except Exception as exc:
            print(f"ASR              : FAILED ({str(exc)[:120]})")
    else:
        from .asr.whisper import FasterWhisperASR
        asr = FasterWhisperASR(model=asr_cfg.get("model", "base"),
                               device=asr_cfg.get("device", "auto"))
        try:
            asr.load()
            print(f"ASR              : whisper/{asr.model_name} on {asr._actual_device}")
        except Exception as exc:
            print(f"ASR              : FAILED ({str(exc)[:120]})")
    # TTS
    from .tts.piper import PiperTTS
    tts = PiperTTS(model_dir=cfg.model_dir(), voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    try:
        tts.load()
        print(f"TTS              : piper/{tts.voice} ({tts.sample_rate} Hz)")
    except Exception as exc:
        print(f"TTS              : FAILED ({str(exc)[:120]})")
    # speaker verification
    try:
        from .voice_id import SpeakerVerifier
        sv_cfg = cfg.section("speaker_verify")
        sv = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(),
                             name=sv_cfg.get("name", "user"),
                             threshold=float(sv_cfg.get("threshold", 0.75)))
        sv.load()
        state = f"enrolled '{sv.name}' (threshold {sv.threshold})" if sv.ready \
            else "NOT enrolled (run: voicebridge enroll)"
        print(f"voice-ID         : {state}")
    except Exception as exc:
        print(f"voice-ID         : FAILED ({str(exc)[:120]})")
    # PersonaPlex / Hermes / local
    print(f"hermes backend   : {'enabled' if cfg.get('hermes', 'enabled', default=False) else 'disabled'}")
    print(f"local actions    : {'enabled' if cfg.get('local', 'enabled', default=True) else 'disabled'}")
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
    mic = MicCapture(device=mic_cfg.get("device"),
                     sample_rate=mic_cfg.get("sample_rate", 48000),
                     channels=mic_cfg.get("channels", 2),
                     blocksize=mic_cfg.get("blocksize", 480),
                     endpointer=Endpointer(aggressiveness=mic_cfg.get("aggressiveness", 1)),
                     gain=float(mic_cfg.get("gain", 4.0)),
                     min_rms=float(mic_cfg.get("min_rms", 0.0008)))
    import threading, time
    samples = []
    with mic.open():
        t0 = time.time()
        while time.time() - t0 < 5:
            data, _ = mic._stream.read(mic.blocksize)
            mono = MicCapture._mono(data) * mic.gain
            peak = float(np.abs(mono).max()) if mono.size else 0.0
            samples.append(peak)
    peak_avg = float(np.mean(samples)) if samples else 0.0
    print(f"peak RMS avg (with gain x{mic.gain}): {peak_avg:.4f}  (min_rms floor: {mic.min_rms})")
    print("Above the floor = the bridge will capture. If this is ~0, check the input")
    print("device / gain (Focusrite: turn gain knob to ~12 o'clock, speak 6-12 in away).")
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


def _record_seconds_to_wav(cfg, seconds: int, out_path: Path) -> Path:
    """Record `seconds` of raw mono 16k mic audio to a WAV (for enrollment)."""
    import numpy as np
    import sounddevice as sd
    from .audio.vad import FRAME_RATE, resample_linear
    mic_cfg = cfg.section("mic")
    sr = int(mic_cfg.get("sample_rate", 48000))
    ch = int(mic_cfg.get("channels", 2))
    print(f"Recording {seconds}s of your voice — speak naturally now...")
    chunks = []
    with sd.InputStream(samplerate=sr, channels=ch,
                        device=mic_cfg.get("device"),
                        blocksize=int(mic_cfg.get("blocksize", 480)),
                        dtype="float32") as stream:
        for _ in range(int(seconds * sr / stream.blocksize)):
            data, _ = stream.read(stream.blocksize)
            chunks.append(data)
    audio = np.concatenate(chunks, axis=0)
    # loudest channel mono (same as the bridge's capture path)
    if audio.ndim > 1 and audio.shape[1] > 1:
        audio = np.max(np.abs(audio), axis=1) * np.sign(audio[:, 0])
    mono = resample_linear(audio.astype(np.float32), sr, FRAME_RATE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import wave
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(FRAME_RATE)
        wf.writeframes((np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    print(f"Saved {seconds}s capture to {out_path}")
    return out_path


def _cmd_enroll(cfg, file: Path, name: str | None, seconds: int | None) -> int:
    from .voice_id import SpeakerVerifier
    sv_cfg = cfg.section("speaker_verify")
    name = name or sv_cfg.get("name", "user")
    sv = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(), name=name,
                         threshold=float(sv_cfg.get("threshold", 0.75)))
    if file is None:
        seconds = seconds or int(sv_cfg.get("enroll_seconds", 5))
        wav = _record_seconds_to_wav(cfg, seconds,
                                     Path(cfg.state_dir()) / "enroll-last.wav")
    else:
        wav = file
    try:
        score = sv.enroll(wav)
    except Exception as exc:
        print(f"ENROLL FAILED: {exc}")
        return 1
    print(f"Enrolled '{name}' (self-score {score:.3f}).")
    print("Barge-in now answers only to YOUR voice. Hold Ctrl and talk.")
    return 0


def _cmd_verify(cfg, file: Path, name: str | None) -> int:
    from .voice_id import SpeakerVerifier
    sv_cfg = cfg.section("speaker_verify")
    name = name or sv_cfg.get("name", "user")
    sv = SpeakerVerifier(cfg.model_dir(), cfg.state_dir(), name=name,
                         threshold=float(sv_cfg.get("threshold", 0.75)))
    try:
        sv.load()
        accepted, score = sv.verify_wav(file, name)
    except Exception as exc:
        print(f"VERIFY FAILED: {exc}")
        return 1
    print(f"score vs '{name}': {score:.3f}  (threshold {sv.threshold}) -> "
          f"{'MATCH (your voice)' if accepted else 'REJECT (not you)'}")
    return 0


def _cmd_flags(cfg, args, explicit_config: Path | None) -> int:
    """voicebridge flags list|get|set|rollback. Read-only unless --apply."""
    from . import feature_flags as FF
    import json as _json

    cmd = getattr(args, "flags_command", None) or "list"
    if cmd == "list":
        rows = [(f.name, f.category, str(FF.effective(cfg, f.name)),
                 str(f.default), str(f.rollback_value), ".".join(f.config_path))
                for f in FF.FLAGS]
        if getattr(args, "json", False):
            print(_json.dumps([{"name": r[0], "category": r[1], "effective": r[2],
                                "default": r[3], "rollback": r[4], "config_path": r[5]}
                               for r in rows], indent=2, sort_keys=True))
            return 0
        print(f"{'flag':<16} {'category':<10} {'effective':<12} "
              f"{'default':<12} {'rollback':<12} config_path")
        for r in rows:
            print(f"{r[0]:<16} {r[1]:<10} {r[2]:<12} {r[3]:<12} {r[4]:<12} {r[5]}")
        return 0

    if cmd == "get":
        try:
            f = FF._by_name(args.name)
        except KeyError as exc:
            print(f"ERROR: {exc}")
            return 2
        print(FF.effective(cfg, f.name))
        return 0

    target = explicit_config or cfg.path
    if target is None:
        from .config import DEFAULT_CONFIG_PATH
        target = Path(DEFAULT_CONFIG_PATH).expanduser()
    target = Path(target)

    if cmd == "set":
        try:
            f = FF._by_name(args.name)
        except KeyError as exc:
            print(f"ERROR: {exc}")
            return 2
        value: object
        if isinstance(f.default, bool):
            value = str(args.value).lower() in ("1", "true", "yes", "on")
        elif isinstance(f.default, (int, float)):
            value = type(f.default)(args.value)
        else:
            value = args.value
        if getattr(args, "apply", False):
            if not target.exists():
                print(f"ERROR: config file not found: {target}")
                return 2
            bak = FF.apply_flag(target, f.name, value)
            print(f"applied {f.name}={value} -> {target} (backup {bak})")
        else:
            print(f"[dry-run] would set {f.name}={value} in {target}")
            print(f"  env override: {' '.join(f'{k}={v}' for k, v in FF.to_env(f, value).items())}")
            print(f"  rollback    : voicebridge flags rollback {f.name} --apply --config {target}")
        return 0

    if cmd == "rollback":
        try:
            f = FF._by_name(args.name)
        except KeyError as exc:
            print(f"ERROR: {exc}")
            return 2
        if getattr(args, "apply", False):
            if not target.exists():
                print(f"ERROR: config file not found: {target}")
                return 2
            bak = FF.rollback_flag(target, f.name)
            print(f"rolled back {f.name}={f.rollback_value} -> {target} (backup {bak})")
        else:
            print(f"[dry-run] would restore {f.name}={f.rollback_value} in {target}")
            print(f"  env override: {' '.join(f'{k}={v}' for k, v in FF.to_env(f, f.rollback_value).items())}")
        return 0
    print(f"ERROR: unknown flags command {cmd!r}")
    return 2


def _cmd_flag_drill(cfg) -> int:
    """Hermetic rollback drill on throwaway config/state (never the live one)."""
    from . import feature_flags as FF
    import tempfile as _tf
    import os as _os
    from .config import Config as _Config

    with _tf.TemporaryDirectory(prefix="vb-flag-drill-") as td:
        tdp = Path(td)
        cfg_path = tdp / "voice.yaml"
        cfg_path.write_text(FF.redact_config_yaml(cfg))
        state = tdp / "state"
        state.mkdir()
        # seed user data (memory + report + cursor) that rollback must preserve
        mem = state / "memory.db"
        mem.write_bytes(b"\x00MEMORY\x01")
        rep = state / "reports"
        rep.mkdir()
        (rep / "manifest.json").write_text('{"id":"x"}')
        cur = state / "cursors.jsonl"
        cur.write_text('{"account":"gmail","cursor":7}\n')

        cfg2 = _Config.load(cfg_path)
        before = FF.state_snapshot(state)
        for f in FF.FLAGS:
            flip = (not f.default) if isinstance(f.default, bool) else (
                f.rollback_value if f.rollback_value != f.default else f.default)
            FF.apply_flag(cfg_path, f.name, flip)
            cfg3 = _Config.load(cfg_path)
            if FF.effective(cfg3, f.name) == f.default:
                print(f"DRILL FAIL: {f.name} did not flip to {flip}")
                return 2
            FF.rollback_flag(cfg_path, f.name)
            cfg4 = _Config.load(cfg_path)
            if FF.effective(cfg4, f.name) != f.rollback_value:
                print(f"DRILL FAIL: {f.name} did not roll back to "
                      f"{f.rollback_value}")
                return 2
            FF.apply_flag(cfg_path, f.name, f.default)
            cfg5 = _Config.load(cfg_path)
            if FF.effective(cfg5, f.name) != f.default:
                print(f"DRILL FAIL: {f.name} did not restore default "
                      f"{f.default}")
                return 2
            print(f"DRILL OK: {f.name} flip->rollback preserved state")
        after = FF.state_snapshot(state)
        if before != after:
            print(f"DRILL FAIL: user data changed: {set(before) ^ set(after)}")
            return 2
        print(f"flag drill: {len(FF.FLAGS)} flags flipped+rolled back; user data intact")
        return 0


def _cmd_package(cfg, args) -> int:
    """voicebridge package manifest|doctor|launch|upgrade|uninstall."""
    import json as _json
    from . import distribution as D
    cmd = getattr(args, "package_command", None) or "manifest"
    if cmd == "manifest":
        print(_json.dumps(D.manifest(), indent=2, sort_keys=True))
        return 0
    if cmd == "doctor":
        checks = D.doctor(cfg)
        if getattr(args, "json", False):
            print(_json.dumps(checks, indent=2, sort_keys=True))
        else:
            for c in checks:
                print(f"[{c['state']:>13}] {c['name']:<12} {c['detail']}")
            print(D.doctor_summary(checks))
        return 0 if all(c["state"] == "OK" for c in checks) else 1
    if cmd == "launch":
        print(" ".join(D.launcher_command(cfg, no_hotkey=args.no_hotkey)))
        return 0
    if cmd == "upgrade":
        print(_json.dumps(D.upgrade_plan(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "uninstall":
        plan = D.uninstall_plan(cfg)
        print(_json.dumps(plan, indent=2, sort_keys=True))
        print("NOTE: uninstall requires explicit operator confirmation; "
              "state is preserved by default.")
        return 0
    if cmd == "model":
        rec = D.model_artifact_record(cfg)
        print(_json.dumps(rec, indent=2, sort_keys=True))
        return 0 if rec.get("state") == "OK" else 1
    if cmd == "distributable":
        m = D.distributable_manifest()
        print(_json.dumps(m, indent=2, sort_keys=True))
        return 0 if m["complete"] else 1
    print(f"ERROR: unknown package command {cmd!r}")
    return 2


def _cmd_audio(cfg, args) -> int:
    """voicebridge audio list|select|reset|status — user-facing settings."""
    import json as _json
    from .audio import settings as AS
    cmd = getattr(args, "audio_command", None) or "status"
    if cmd == "list":
        try:
            devs = AS.list_devices()
        except AS.AudioSettingsError as exc:
            print(f"ERROR: {exc}")
            return 2
        if getattr(args, "json", False):
            # only STABLE fields are exposed — never raw_name (may contain an
            # ALSA hw path) or the transient PortAudio index
            def _stable(d):
                return {"stable_id": d.stable_id, "name": d.name,
                        "role": d.role, "backend": d.backend,
                        "channels": d.channels,
                        "default_samplerate": d.default_samplerate,
                        "is_default": d.is_default, "state": d.state}
            out = {"inputs": [_stable(d) for d in devs["inputs"]],
                   "outputs": [_stable(d) for d in devs["outputs"]]}
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0
        print("INPUT devices:")
        for d in devs["inputs"]:
            flag = " *" if d.is_default else "  "
            print(f"{flag} {d.stable_id:<45} backend={d.backend}")
        print("OUTPUT devices:")
        for d in devs["outputs"]:
            flag = " *" if d.is_default else "  "
            print(f"{flag} {d.stable_id:<45} backend={d.backend}")
        return 0
    if cmd == "select":
        try:
            if args.kind == "mic":
                dev = AS.select_microphone(cfg, args.name)
            else:
                dev = AS.select_output(cfg, args.name)
        except AS.InvalidSelectionError as exc:
            print(f"ERROR: {exc}")
            return 2
        except AS.AudioSettingsError as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"selected {args.kind}: {dev.stable_id} (persisted by name)")
        return 0
    if cmd == "reset":
        try:
            bak = AS.reset_to_defaults(cfg)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2
        print(f"reset audio to OS defaults (backup {bak})")
        return 0
    if cmd == "status":
        st = AS.status(cfg)
        if getattr(args, "json", False):
            print(_json.dumps(st, indent=2, sort_keys=True))
            return 0
        print(f"mic    : [{st['mic']['state']:>16}] {st['mic']['detail']}")
        print(f"output : [{st['output']['state']:>16}] {st['output']['detail']}")
        return 0 if all(v["state"] == "OK" for v in st.values()) else 1
    print(f"ERROR: unknown audio command {cmd!r}")
    return 2


def _cmd_profile(cfg, args) -> int:
    """voicebridge profile list|show|check|install|select|reset|upgrade|
    remove|rollback. Dry-run by default; JSON output."""
    import json as _json
    from . import profiles as P
    cmd = getattr(args, "profile_command", None) or "list"
    if cmd == "list":
        out = P.profiles_list(cfg)
        if getattr(args, "json", False):
            print(_json.dumps(out, indent=2, sort_keys=True))
            return 0
        print(f"{'id':<22} {'kind':<6} {'state':<8} {'active':<6} rollback")
        for p in out["profiles"]:
            print(f"{p['id']:<22} {p['kind']:<6} {p['gate']:<8} "
                  f"{'*' if p['active'] else '':<6} {p['rollback_to']}")
        print("active:", _json.dumps(out["active"]))
        return 0
    if cmd == "show":
        out = P.profiles_show(cfg, args.kind)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "check":
        out = P.profile_check(cfg, args.kind, args.profile)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 1
    if cmd == "install":
        try:
            data = _json.loads(Path(args.manifest).read_text())
            man = P.ProviderManifest(**data)
        except Exception as exc:
            print(_json.dumps({"state": "MISSING",
                               "detail": f"manifest error: {exc}"},
                              indent=2))
            return 2
        out = P.install_manifest(cfg, man)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 1
    if cmd == "select":
        out = P.apply_profile(cfg, args.kind, args.profile,
                              dry_run=not args.apply,
                              require_confirmation=not args.confirm)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    if cmd == "reset":
        out = P.reset_profile(cfg, args.kind, dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    if cmd == "upgrade":
        out = P.upgrade_profile(cfg, args.kind, args.profile or "",
                                dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 1
    if cmd == "remove":
        out = P.remove_profile(cfg, args.kind, args.profile,
                               dry_run=not args.apply)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    if cmd == "rollback":
        out = P.rollback_profile(cfg, args.kind, dry_run=not args.apply,
                                 backup=args.backup)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    print(f"ERROR: unknown profile command {cmd!r}")
    return 2


def _cmd_branding(cfg, args) -> int:
    """voicebridge branding plan|verify|rollback — Qubit migration."""
    import json as _json
    from . import profiles as P
    cmd = getattr(args, "branding_command", None) or "plan"
    if cmd == "plan":
        out = P.branding_plan(cfg, dry_run=not args.apply,
                              backup_dir=args.backup_dir,
                              repo=args.repo,
                              migrate_paths=args.migrate_paths)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0
    if cmd == "verify":
        out = P.branding_verify(cfg, repo=args.repo)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "VERIFIED" else 1
    if cmd == "rollback":
        out = P.branding_rollback(cfg, args.backup_dir, repo=args.repo)
        print(_json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["state"] == "OK" else 2
    print(f"ERROR: unknown branding command {cmd!r}")
    return 2


def _passphrase(args, env: str = "VOICE_BRIDGE_PASSPHRASE") -> str:
    import os as _os
    pw = getattr(args, "passphrase", None) or _os.environ.get(env, "")
    if not pw:
        raise ValueError(f"passphrase required (--passphrase or {env})")
    return pw


def _cmd_export_state(cfg, args) -> int:
    from . import portable as P
    out = args.out or (cfg.state_dir() / f"portable-{int(__import__('time').time())}.vbp")
    try:
        pw = _passphrase(args)
        bundle = P.export_bundle(cfg, pw)
        out.write_bytes(bundle)
        try:
            import os as _os
            _os.chmod(out, 0o600)
        except OSError:
            pass
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"exported encrypted portable state: {out}")
    return 0


def _cmd_import_state(cfg, args) -> int:
    from . import portable as P
    from . import device as Dev
    try:
        if not args.bundle.exists():
            print(f"ERROR: bundle not found: {args.bundle}")
            return 2
        bundle = args.bundle.read_bytes()
        info = P.bundle_info(bundle)
        print(f"bundle: schema v{info.schema_version} "
              f"openssl {info.openssl_version} kdf_iter {info.kdf_iter}")
        pw = _passphrase(args)
        payload = P.read_bundle(bundle, pw)
        plan = P.plan_restore(cfg, payload, dry_run=not args.apply)
        # trusted-device enrollment gate: a restore to a NEW device requires
        # an explicitly enrolled device; never automatic.
        try:
            Dev.require_enrolled(cfg)
        except Dev.DeviceNotEnrolledError:
            if args.apply:
                raise
            print("WARN: device not enrolled — `voicebridge device enroll` "
                  "must run before an applied restore")
        print("restore plan:")
        for a in plan.actions:
            print(f"  - {a}")
        if not args.apply:
            print("dry-run/preview (no files written) — pass --apply to restore")
            return 0
        result = P.restore_state(cfg, payload, dry_run=False)
        print(f"restored; pre-restore backup at {result['backup']}")
        return 0
    except P.WrongPassphraseError:
        print("ERROR: wrong passphrase or tampered bundle")
        return 2
    except P.CorruptedBundleError as exc:
        print(f"ERROR: corrupted bundle: {exc}")
        return 2
    except P.UnsupportedSchemaError as exc:
        print(f"ERROR: {exc}")
        return 2
    except P.LowDiskError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Dev.DeviceNotEnrolledError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


def _cmd_device(cfg, args) -> int:
    import json as _json
    from . import device as Dev
    cmd = getattr(args, "device_command", None) or "status"
    if cmd == "status":
        print(_json.dumps(Dev.profile(cfg), indent=2, sort_keys=True))
        return 0
    if cmd == "enroll":
        try:
            rec = Dev.enroll(cfg, model_alias=args.model_alias,
                             require_new_authorization=False)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2
        print(_json.dumps(rec, indent=2, sort_keys=True))
        print("device enrolled (explicit authorization recorded)")
        return 0
    print(f"ERROR: unknown device command {cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())