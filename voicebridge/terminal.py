"""qubit-dev terminal — hermes/openclaw-style CLI for the voice bridge.

Commands (inside the REPL every line starting with / is a slash command):
  /help                  — this help
  /status                — gateway + keep_alive + hermes state
  /activity [limit] [q]  — tail the activity feed (like Dashboard Activity)
  /logs [limit] [q]      — tail events.jsonl (the source of truth, like in Dashboard Logs)
  /interrupt [mode]      — interrupt the current task (mode: interrupt|stop)
  /talk <text>           — send text as if it were voice (no mic)
  /clear                 — clear screen
  /quit, /exit           — leave

Bare text without / is treated as /talk <text>, same as hermes chat.
Flags:  --attach  starts the bridge in-process so /talk is synchronous (no HTTP hop).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

CMD_HELP = """\
qubit-dev — Qubit voice-bridge terminal (like hermes / openclaw)
Usage:
  qubit-dev                  interactive REPL
  qubit-dev --attach         REPL with bridge attached (sync /talk)
  qubit-dev /status          one-shot status
  qubit-dev /logs  [args]    one-shot logs
  qubit-dev "hello there"    one-shot talk (bare text)

Slash commands inside the REPL:
  /help                      this help
  /status                    bridge + gateway + hermes state
  /health [window_s]         live health (status + recent errors + ASR/turn timings)
  /watch [rows]              live activity tail (run in a loop; Ctrl-C to stop)
  /activity [limit] [q]      recent activity (limit default 20)
  /logs [limit] [q]          tail events.jsonl (e.g. /logs 50 hermes)
  /interrupt [stop]          interrupt current task
  /talk <text>               send text as voice
  /clear                     clear screen
  /quit  /exit                leave
"""


def _load_cfg(config_path: Optional[Path] = None):
    from voicebridge.config import Config
    from voicebridge.logging_setup import setup
    cfg = Config.load(config_path)
    # Ensure the journal is wired so every helper sees the same path.
    setup(state_dir=cfg.state_dir())
    return cfg


def _status_text(cfg) -> str:
    lines = []
    try:
        from voicebridge.openclaw.discovery import discover_gateway
        info = discover_gateway(cfg)
        lines.append(f"gateway: {info.url or '(none)'}  token: {'found' if info.token else 'MISSING'}  agent: {info.agent}  model: {info.default_model or '(default)'}")
    except Exception as e:
        lines.append(f"gateway: error {e}")
    try:
        import urllib.request, json as _json
        j = _json.load(urllib.request.urlopen("http://127.0.0.1:8890/api/status", timeout=3))
        lines.append(f"dashboard: bridge={j.get('bridge')} route={j.get('route')} busy={j.get('busy')} web_active={j.get('web_active')} uptime={j.get('uptime_s')}s")
    except Exception as e:
        lines.append(f"dashboard: offline ({e})")
    try:
        import urllib.request, json as _json
        j = _json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3))
        for m in j.get("models", []):
            lines.append(f"ollama: {m.get('name')} keep_alive until {m.get('expires_at','?')}")
        if not j.get("models"):
            lines.append("ollama: no models loaded")
    except Exception as e:
        lines.append(f"ollama: {e}")
    try:
        hv = cfg.get("hermes", "enabled", default=False)
        lines.append(f"hermes: {'enabled' if hv else 'disabled'} (ask hermes ...)")
    except Exception:
        pass
    return "\n".join(lines)


def _health_text(cfg, since_s: float = 3600.0) -> str:
    """Live health snapshot: status + recent error/timing events (verifies the
    voice path is healthy and nothing errored recently)."""
    lines = []
    try:
        import urllib.request, json as _json
        j = _json.load(urllib.request.urlopen("http://127.0.0.1:8890/api/status", timeout=3))
        lines.append(f"dashboard: bridge={j.get('bridge')} route={j.get('route')} busy={j.get('busy')} "
                     f"web_active={j.get('web_active')} awaiting={j.get('awaiting_input')} "
                     f"uptime={j.get('uptime_s')}s tts={j.get('tts')}")
    except Exception as e:
        lines.append(f"dashboard: offline ({e})")
    # Scan events.jsonl for recent failures / slow turns / empty transcripts.
    state = Path(cfg.state_dir()) / "events.jsonl"
    now = time.time()
    want = {"fail", "error", "timeout", "failed", "empty", "capture_error", "interrupt"}
    errs = []
    timings = []
    last_ts = 0.0
    last_event = ""
    if state.exists():
        try:
            for line in state.read_text(errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts = float(ev.get("ts") or 0)
                if ts:
                    if ts > last_ts:
                        last_ts = ts
                        last_event = ev.get("event", "")
                    if now - ts > since_s:
                        continue
                    evt = (ev.get("event") or "").lower()
                    if any(w in evt for w in want) and evt not in errs and len(errs) < 6:
                        errs.append(f"{time.strftime('%H:%M:%S', time.localtime(ts))} {ev.get('event')} "
                                    f"{str(ev.get('error') or ev.get('reason') or '')[:60]}")
                    if "timing" in evt or "turn_done" in evt:
                        timings.append(ev)
        except Exception:
            pass
    lines.append(f"logs: last event {time.strftime('%H:%M:%S', time.localtime(last_ts)) if last_ts else '?'} "
                 f"({last_event or 'none'})  errors in last {int(since_s)}s: {len(errs)}")
    if timings:
        asr = [t.get("asr_ms") for t in timings if t.get("asr_ms")]
        total = [t.get("total_ms") for t in timings if t.get("total_ms")]
        if asr:
            lines.append(f"asr: avg {round(sum(asr)/len(asr))}ms across {len(asr)} turns")
        if total:
            lines.append(f"turn: avg {round(sum(total)/len(total))}ms across {len(total)} turns")
    for e in errs:
        lines.append(f"  ! {e}")
    if not errs:
        lines.append("  ✓ no recent errors")
    return "\n".join(lines)


def _watch_text(cfg, args, interval_s: float = 2.0, max_rows: int = 8) -> str:
    """One-shot 'watch' snapshot: tail the newest activity feed rows (like a live
    log). Best used in a loop; printing only the newest rows keeps it readable."""
    lim = int(args[0]) if args and args[0].isdigit() else max_rows
    q = " ".join(args[1:] if args and args[0].isdigit() else args)
    items = _activity_items(cfg, limit=lim, query=q)
    if not items:
        return "(no activity yet — waiting for voice/tasks…) [hit Ctrl-C to stop]"
    return _format_activity(items) + "\n[Ctrl-C to stop watching]"


def _activity_items(cfg, limit: int = 20, query: str = ""):
    # Prefer live dashboard feed when bridge is running, fall back to journal tail.
    try:
        import urllib.request, json as _json
        url = f"http://127.0.0.1:8890/api/activity?limit={limit}"
        j = _json.load(urllib.request.urlopen(url, timeout=3))
        items = j.get("items", []) or []
        if query:
            q = query.lower()
            items = [it for it in items if q in json.dumps(it).lower()]
        return items
    except Exception:
        pass
    # Fallback: read journal and map like webui does.
    try:
        from voicebridge.webui import _event_to_feed_item
        p = cfg.state_dir() / "events.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(errors="replace").splitlines()[-max(limit * 6, 4000):]
        out = []
        for l in lines:
            try:
                ev = json.loads(l)
            except Exception:
                continue
            it = _event_to_feed_item(ev)
            if it is None:
                continue
            out.append(it)
        if query:
            q = query.lower()
            out = [it for it in out if q in json.dumps(it).lower()]
        return out[-limit:][::-1]
    except Exception as e:
        return [{"kind": "error", "text": str(e)}]


def _logs_items(cfg, limit: int = 50, query: str = ""):
    p = cfg.state_dir() / "events.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(errors="replace").splitlines()[-max(limit * 4, 8000):]
    out = []
    q = query.lower() if query else ""
    for l in lines:
        try:
            ev = json.loads(l)
        except Exception:
            continue
        if q and q not in json.dumps(ev).lower():
            continue
        out.append(ev)
    return out[-limit:]


def _format_activity(items) -> str:
    if not items:
        return "(no activity)"
    out = []
    for it in items:
        ts = it.get("ts")
        when = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
        kind = it.get("kind", "?")
        route = it.get("route", "")
        text = (it.get("text") or it.get("summary") or "")[:140].replace("\n", " ")
        raw = it.get("raw")
        tag = f"[{kind}/{route}]" if route else f"[{kind}]"
        line = f"{when} {tag} {text}"
        if raw:
            line += f"  (raw {len(raw)} chars)"
        out.append(line)
    return "\n".join(out)


def _format_logs(items) -> str:
    if not items:
        return "(no logs)"
    out = []
    for ev in items:
        ts = ev.get("ts")
        when = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else ""
        evt = ev.get("event", "?")
        fields = {k: v for k, v in ev.items() if k not in ("ts", "event")}
        # Compact one-liner: keep terminal readable.
        if isinstance(fields.get("text"), str) and len(fields["text"]) > 120:
            fields["text"] = fields["text"][:120] + "…"
        if isinstance(fields.get("summary"), str) and len(fields["summary"]) > 120:
            fields["summary"] = fields["summary"][:120] + "…"
        out.append(f"{when} {evt} {json.dumps(fields, ensure_ascii=False)}")
    return "\n".join(out)


def _do_interrupt(mode: str = "interrupt") -> str:
    try:
        import urllib.request, json as _json
        body = json.dumps({"mode": mode}).encode()
        req = urllib.request.Request("http://127.0.0.1:8890/api/interrupt", data=body, headers={"Content-Type": "application/json"})
        j = _json.load(urllib.request.urlopen(req, timeout=5))
        return json.dumps(j)
    except Exception as e:
        return f"interrupt failed: {e}"


def _do_talk(cfg, text: str, orch=None) -> str:
    text = text.strip()
    if not text:
        return "(empty)"
    # If attached, run synchronously through the orchestrator.
    if orch is not None:
        try:
            res = orch.process_web_utterance(text)
            summ = res.get("summary") or res.get("reply") or ""
            route = res.get("route", "")
            return f"[{route}] {summ}" if summ else json.dumps(res, ensure_ascii=False)
        except Exception as e:
            return f"talk error: {e}"
    # Otherwise go through HTTP (works even when bridge runs as a service).
    try:
        import urllib.request, json as _json
        body = _json.dumps({"text": text}).encode()
        req = urllib.request.Request("http://127.0.0.1:8890/api/type", data=body, headers={"Content-Type": "application/json"})
        rid = _json.load(urllib.request.urlopen(req, timeout=10))["request_id"]
        for _ in range(30):
            time.sleep(0.45)
            pr = _json.load(urllib.request.urlopen(f"http://127.0.0.1:8890/api/result/{rid}", timeout=7))
            if pr.get("status") == "done":
                summ = pr.get("summary") or ""
                route = pr.get("route", "")
                extra = f" ({pr.get('latency_s', '?')}s)" if pr.get("latency_s") is not None else ""
                return f"[{route}]{extra} {summ}" if summ else json.dumps(pr, ensure_ascii=False)
        return "timeout waiting for result"
    except Exception as e:
        return f"talk failed: {e}"


def handle_slash(line: str, cfg, orch=None) -> Optional[str]:
    """Execute one slash command. Returns text to print, or None to signal quit."""
    raw = line.strip()
    if not raw:
        return ""
    # Allow bare text as talk
    if not raw.startswith("/"):
        return _do_talk(cfg, raw, orch=orch)
    parts = raw.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("/quit", "/exit", "/q"):
        return None
    if cmd == "/help":
        return CMD_HELP
    if cmd == "/clear":
        os.system("clear" if os.name != "nt" else "cls")
        return ""
    if cmd == "/status":
        return _status_text(cfg)
    if cmd == "/health":
        return _health_text(cfg)
    if cmd == "/watch":
        return _watch_text(cfg, args)
    if cmd in ("/activity", "/a"):
        lim = int(args[0]) if args and args[0].isdigit() else 20
        q = " ".join(args[1:] if args and args[0].isdigit() else args)
        return _format_activity(_activity_items(cfg, limit=lim, query=q))
    if cmd in ("/logs", "/log", "/l"):
        lim = int(args[0]) if args and args[0].isdigit() else 50
        q = " ".join(args[1:] if args and args[0].isdigit() else args)
        return _format_logs(_logs_items(cfg, limit=lim, query=q))
    if cmd == "/interrupt":
        mode = args[0] if args else "interrupt"
        return _do_interrupt(mode=mode)
    if cmd == "/talk":
        return _do_talk(cfg, " ".join(args), orch=orch)
    return f"unknown command {cmd!r} — try /help"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qubit-dev", description="Qubit voice-bridge terminal (like hermes/openclaw). Bare text = talk. Slash commands: /help /status /logs /activity /interrupt /talk /clear /quit")
    p.add_argument("text", nargs="*", help="one-shot talk text (bare) or slash command like /status")
    p.add_argument("--config", type=Path, default=None, help="path to voice.yaml")
    p.add_argument("--attach", action="store_true", help="attach bridge in-process so /talk is synchronous")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = _load_cfg(args.config)

    # One-shot mode: qubit-dev /status  or  qubit-dev "hello"
    if args.text:
        line = " ".join(args.text)
        # If it looks like a slash command, run it once and exit.
        if line.strip().startswith("/"):
            out = handle_slash(line, cfg, orch=None)
            if out is not None and out != "":
                print(out)
            return 0
        # Bare text → one-shot talk
        print(_do_talk(cfg, line, orch=None))
        return 0

    # Interactive REPL
    orch = None
    if args.attach:
        try:
            from voicebridge.orchestrator import VoiceOrchestrator
            orch = VoiceOrchestrator(cfg, no_hotkey=True)
            orch.start()
            print("bridge attached (gateway + ASR/TTS ready). Bare text = /talk. Try /help")
        except Exception as e:
            print(f"attach failed: {e} — continuing without bridge (HTTP mode).")

    print("qubit-dev — Qubit voice-bridge terminal. Type /help for commands. Bare text = talk. Ctrl-D to quit.")
    # History file
    hist_path = Path.home() / ".local/state/qubit-dev/history"
    hist_path.parent.mkdir(parents=True, exist_ok=True)

    # Try prompt_toolkit for nice editing; fall back to input().
    use_ptk = False
    session = None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        session = PromptSession(history=FileHistory(str(hist_path)))
        use_ptk = True
    except Exception:
        try:
            import readline  # noqa: F401
            if hist_path.exists():
                try:
                    import readline as _rl
                    _rl.read_history_file(str(hist_path))
                except Exception:
                    pass
        except Exception:
            pass

    while True:
        try:
            if use_ptk and session is not None:
                line = session.prompt("qubit-dev> ")
            else:
                line = input("qubit-dev> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        # Persist history for readline fallback
        if not use_ptk:
            try:
                import readline as _rl
                _rl.write_history_file(str(hist_path))
            except Exception:
                pass
        out = handle_slash(line, cfg, orch=orch)
        if out is None:  # quit
            break
        if out:
            print(out)

    if orch is not None:
        try:
            orch.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
