"""Local PC actions: direct, safe, reversible machine control.

Only whitelisted actions live here. Everything risky (rm, git reset --hard,
package installs, browser actions with logins/payments, service restarts,
file deletion) is *not* implemented locally — those run as supervised
worker sessions where the safety policy + spoken confirmations apply.

Each action returns a short spoken status string.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..logging_setup import get_logger, log_event

log = get_logger()


# ---------------------------------------------------------------------------
# Operator-local overrides (Packet 8K portability).
#
# Personal launcher targets and project roots are machine-specific and MUST
# NOT ship in distributable runtime code. They live in an operator-local YAML
# at ~/.config/qubit-voicebridge/local-actions.yaml (honours
# $XDG_CONFIG_HOME; legacy openclaw-voice-bridge/qubit-voice-agent paths are
# read as fallback). The installed package works on any machine with portable
# generic defaults (launchers by name, home-directory file/screenshot dirs);
# the operator override only customises that operator's machine.
# ---------------------------------------------------------------------------
def _operator_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    canon = base / "qubit-voicebridge" / "local-actions.yaml"
    if canon.exists():
        return canon
    for legacy in ("qubit-voice-agent", "openclaw-voice-bridge"):
        cand = base / legacy / "local-actions.yaml"
        if cand.exists():
            return cand
    return canon


def _load_operator_overrides() -> Dict[str, Any]:
    path = _operator_config_path()
    data: Dict[str, Any] = {}
    try:
        if path.is_file():
            import yaml
            raw = yaml.safe_load(path.read_text())
            if isinstance(raw, dict):
                data = raw
    except Exception as exc:
        log_event("local.actions.config_error", error=str(exc)[:160])
    return data


_OPERATOR = _load_operator_overrides()


def _expanded(args: List[str]) -> List[str]:
    return [os.path.expanduser(a) for a in args]


def _app_targets() -> Dict[str, List[str]]:
    home = str(Path.home())
    targets: Dict[str, List[str]] = {
        "browser": ["xdg-open", "https://duckduckgo.com"],
        "terminal": ["gnome-terminal"],
        "discord": ["discord"],
        "telegram": ["telegram-desktop"],
        "spotify": ["spotify"],
        # Batch-2 desktop shortcuts: present on this host (shutil.which
        # verified); the handler refuses honestly where the binary is absent.
        "calculator": ["gnome-calculator"],
        "settings": ["gnome-control-center"],
        "system monitor": ["gnome-system-monitor"],
        "clocks": ["gnome-clocks"],
    }
    targets.update(_OPERATOR.get("app_targets") or {})
    targets.setdefault("editor", ["xdg-open", home])
    targets.setdefault("vscode", ["code", home])
    targets.setdefault("cursor", ["cursor"])
    targets.setdefault("chromium", ["chromium"])
    targets.setdefault("files", ["xdg-open", home])
    return targets


def _project_roots() -> Dict[str, str]:
    return {k: str(v) for k, v in (_OPERATOR.get("project_roots") or {}).items()}


def _screenshot_dir() -> Path:
    over = _OPERATOR.get("screenshot_dir")
    if over:
        return Path(os.path.expanduser(str(over)))
    return Path.home() / "Pictures"


@dataclass
class ActionMatch:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass
class LocalAction:
    name: str
    desc: str
    fn: Callable[[Dict[str, Any]], str]
    patterns: List[str] = field(default_factory=list)
    confirm: bool = False  # local actions are all safe by construction; kept for future


def _run(args: List[str], timeout: float = 10.0) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.stdout.strip()


def _pactl(args: List[str]) -> str:
    return _run(["pactl"] + args)


def _a_volume_up(_: Dict[str, Any]) -> str:
    _pactl(["set-sink-volume", "@DEFAULT_SINK@", "+10%"])
    return "Volume up."


def _a_volume_down(_: Dict[str, Any]) -> str:
    _pactl(["set-sink-volume", "@DEFAULT_SINK@", "-10%"])
    return "Volume down."


def _a_volume_mute(_: Dict[str, Any]) -> str:
    _pactl(["set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    return "Speaker mute toggled."


def _a_mic_mute(_: Dict[str, Any]) -> str:
    _pactl(["set-source-mute", "@DEFAULT_SOURCE@", "toggle"])
    return "Microphone mute toggled."


#: Desktop shortcuts whose launcher binary may be absent: the handler
#: refuses honestly instead of raising FileNotFoundError. Existing entries
#: keep their launch-attempt behavior (their tests mock Popen, not PATH).
_APP_REQUIRE_BINARY = frozenset(
    {"calculator", "settings", "system monitor", "clocks"})


def _a_open_app(params: Dict[str, Any]) -> str:
    app = params.get("app", "")
    targets = _app_targets()
    argv = targets.get(app)
    if not argv:
        return f"I don't have a launcher for {app or 'that'}."
    if app in _APP_REQUIRE_BINARY and not shutil.which(argv[0]):
        return (f"{app} isn't installed "
                f"(missing {argv[0]}).")
    try:
        subprocess.Popen(_expanded(argv), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return f"Opening {app}."
    except Exception as exc:
        return f"Could not open {app}: {exc}"


def _a_open_project(params: Dict[str, Any]) -> str:
    """'open the <name> project in vscode' -> launch VS Code on that repo.

    ``<name>`` is matched against the operator-configured ``project_roots``;
    no project names are baked into the product source.
    """
    roots = _project_roots()
    name = (params.get("project", "") or "").strip().lower()
    root = roots.get(name)
    if not root:
        # fuzzy: pick the first root whose name appears in what was said
        said = (params.get("project", "") or "").lower()
        for key, path in roots.items():
            if key in said and len(key) > 2:
                root = path
                break
    if not root:
        known = ", ".join(sorted(set(roots))) or "none configured"
        return (f"I know these projects: {known}. "
                "Which one should I open?")
    try:
        subprocess.Popen(["code", os.path.expanduser(str(root))],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opening {name or 'project'} in VS Code."
    except Exception as exc:
        return f"Could not open VS Code: {exc}"


def _screenshot_candidates(out: str) -> List[List[str]]:
    """Ordered on-PATH backends, each writing exactly to ``out``.

    ``import`` is X11-only and requires ``DISPLAY``; the ``xwd | convert``
    fallback requires both binaries. Only what actually exists on PATH is
    offered, so a missing tool is never invoked.
    """
    candidates: List[List[str]] = []
    if shutil.which("gnome-screenshot"):
        candidates.append(["gnome-screenshot", "-f", out])
    if shutil.which("scrot"):
        candidates.append(["scrot", out])
    if shutil.which("spectacle"):
        candidates.append(["spectacle", "-b", "-n", "-o", out])
    if shutil.which("grim"):
        candidates.append(["grim", out])
    if shutil.which("maim"):
        candidates.append(["maim", out])
    if os.environ.get("DISPLAY") and shutil.which("import"):
        candidates.append(["import", "-window", "root", out])
    if shutil.which("xwd") and shutil.which("convert"):
        cmd = "xwd -root -silent | convert xwd:- %s" % shlex.quote(out)
        candidates.append(["sh", "-c", cmd])
    return candidates


def _screenshot_written(out: str) -> bool:
    """True only when a real, non-empty file exists at ``out``."""
    p = Path(out)
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _a_screenshot(_: Dict[str, Any]) -> str:
    d = _screenshot_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "Screenshot folder unavailable."
    out = str(d / ("screenshot-%s.png" % __import__("time").strftime("%Y%m%d-%H%M%S")))
    for argv in _screenshot_candidates(out):
        try:
            _run(argv)
        except Exception:
            pass
        if _screenshot_written(out):
            return "Screenshot saved."
        # drop an empty/partial artifact so the next backend starts clean
        try:
            p = Path(out)
            if p.exists() and p.stat().st_size == 0:
                p.unlink()
        except OSError:
            pass
    return ("No screenshot tool found "
            "(install scrot/gnome-screenshot/ImageMagick).")


# --- YouTube playback state + controls ------------------------------------
_YT_MODE = None          # None=unset, "tab"=windowed Chromium tab, "audio"=audio-only background
_YT_PENDING = None       # resolved candidates waiting for the user to pick a mode
_YT_PLAYING = None       # {query, results, idx, url, title, mode}

def _yt_ask_mode(query: str, ranked: list) -> str:
    """If no play mode is set, stash the resolved track and ask how to play it."""
    global _YT_PENDING
    _YT_PENDING = {"query": query, "ranked": ranked}
    return ("Should I play that in a windowed browser tab, or quietly in the "
            "background with just the audio?")

def _yt_launch(query: str, ranked: list, idx: int, mode: str) -> str:
    """Actually start playback for the chosen track in the given mode."""
    global _YT_PLAYING
    win = ranked[idx] if idx < len(ranked) else ranked[-1]
    _YT_PLAYING = {"query": query, "ranked": ranked, "idx": idx,
                   "url": win.get("url", ""), "title": win.get("title", ""), "mode": mode}
    if mode == "tab":
        try:
            import sys as _sys
            from pathlib import Path
            _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            import browser_auto
            res = browser_auto.open_url(win.get("url", ""))
            if res.get("error") is None and res.get("url"):
                log_event("play.youtube.tab", query=query[:160], title=win.get("title")[:80],
                          url=win.get("url"), mode=mode)
                return f"Playing {win.get('title', query)} in a tab."
        except Exception as exc:
            log_event("play.youtube.tab_error", error=str(exc)[:160])
    # audio-only / tab-fallback: stream via ffplay in the background
    subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", win.get("url", "")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, stdin=subprocess.DEVNULL)
    log_event("play.youtube.audio", query=query[:160], title=win.get("title")[:80], mode=mode)
    return f"Playing {win.get('title', query)} quietly in the background."

def _a_play_youtube_params(params: Dict[str, Any]) -> str:
    """Resolve search + start playback in the stored mode (or first ask)."""
    query = (params.get("query") or params.get("text") or "").strip()
    if not query:
        return "What would you like me to play?"
    if not shutil.which("yt-dlp"):
        return "yt-dlp is not installed."
    top_n = 5
    try:
        proc = subprocess.run(
            ["yt-dlp", "--no-playlist", "--dump-json", "--playlist-items",
             f"1-{top_n}", f"ytsearch{top_n}:{query}"],
            capture_output=True, text=True, timeout=60.0)
        results = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json as _j
                d = _j.loads(line)
            except Exception:
                continue
            results.append({
                "title": d.get("title", ""),
                "url": d.get("webpage_url", ""),
                "views": d.get("view_count") or 0,
                "uploader": d.get("uploader", ""),
            })
    except Exception as exc:
        log_event("play.youtube.resolve_failed", error=str(exc)[:160])
        return "I had trouble searching YouTube."
    if not results:
        return "I couldn't find that on YouTube."
    ranked = sorted(results, key=lambda r: (r.get("views") or 0), reverse=True)
    if _YT_MODE is None:
        return _yt_ask_mode(query, ranked)
    return _yt_launch(query, ranked, 0, _YT_MODE)

def _a_yt_mode(params: Dict[str, Any]) -> str:
    """Answer to 'windowed tab or background?': resolve the pending track in that mode."""
    global _YT_MODE, _YT_PENDING
    text = ((params.get("text") or "") + " " + (params.get("query") or "")).strip().lower()
    if any(w in text for w in ("background", "audio", "quiet", "quietly", "just audio", "audio only", "no tab", "sound only", "behind", "headless", "nonstop")):
        mode = "audio"
    elif any(w in text for w in ("window", "tab", "browser", "open", "screen", "windowed", "show", "see", "video", "watch")):
        mode = "tab"
    else:
        return "In a windowed tab, or quietly in the background?"
    _YT_MODE = mode
    if _YT_PENDING is None:
        return f"Got it — I'll play in {'a tab' if mode == 'tab' else 'the background'} from now on."
    pending = _YT_PENDING
    _YT_PENDING = None
    return _yt_launch(pending["query"], pending["ranked"], 0, mode)

def _yt_play_pause_js(mute: bool) -> str:
    action = "pause" if mute else "play"
    return ("(function(){var v=document.querySelector('video');if(v){v." + action +
            "();return 'ok';}return 'no-video';})()")

def _yt_signal_player(sig: str) -> None:
    if not shutil.which("pgrep"):
        return
    try:
        pids = subprocess.run(["pgrep", "-af", "ffplay"],
                              capture_output=True, text=True).stdout.splitlines()
        for line in pids:
            pid = line.split()[0]
            try:
                subprocess.run(["kill", "-s", sig, pid], capture_output=True)
            except Exception:
                pass
    except Exception:
        pass

def _a_youtube_pause(_: Dict[str, Any]) -> str:
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import browser_auto
        browser_auto.run_js("youtube.com", _yt_play_pause_js(True))
    except Exception as exc:
        log_event("play.youtube.pause_error", error=str(exc)[:160])
    _yt_signal_player("SIGSTOP")
    return "Paused."

def _a_youtube_resume(_: Dict[str, Any]) -> str:
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import browser_auto
        browser_auto.run_js("youtube.com", _yt_play_pause_js(False))
    except Exception as exc:
        log_event("play.youtube.resume_error", error=str(exc)[:160])
    _yt_signal_player("SIGCONT")
    return "Resumed."

def _a_youtube_next(_: Dict[str, Any]) -> str:
    if not _YT_PLAYING:
        return "Nothing is playing right now."
    ranked = _YT_PLAYING.get("ranked") or []
    idx = (_YT_PLAYING.get("idx") or 0) + 1
    if idx >= len(ranked):
        return "That's the last one I found. Say play <something> for more."
    mode = _YT_PLAYING.get("mode") or _YT_MODE or "tab"
    return _yt_launch(_YT_PLAYING.get("query", ""), ranked, idx, mode)

def _a_youtube_stop(_: Dict[str, Any]) -> str:
    global _YT_PLAYING
    _yt_signal_player("SIGKILL")
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import browser_auto
        browser_auto.close_tabs("youtube.com")
    except Exception as exc:
        log_event("play.youtube.stop_error", error=str(exc)[:160])
    _YT_PLAYING = None
    return "Music stopped and YouTube tabs closed."


def _a_clipboard(_: Dict[str, Any]) -> str:
    if shutil.which("xclip"):
        return _run(["xclip", "-o", "-selection", "clipboard"])[:300] or "Clipboard is empty."
    return "xclip not installed."


def _a_lock(_: Dict[str, Any]) -> str:
    _run(["loginctl", "lock-session"])
    return "Locking the screen."


def _a_brightness_up(_: Dict[str, Any]) -> str:
    # try brightnessctl, then light, then xrandr fallback
    if shutil.which("brightnessctl"):
        _run(["brightnessctl", "set", "+10%"])
        return "Brightness up."
    if shutil.which("light"):
        _run(["light", "-A", "10"])
        return "Brightness up."
    # xrandr: bump primary output by 0.1 (clamped 0.3-1.0)
    if shutil.which("xrandr"):
        out = _run(["xrandr", "--verbose"])
        import re
        m = re.search(r"^\S+ connected", out, re.M)
        if m:
            name = m.group(0).split()[0]
            b = _run(["xrandr", "--verbose"])
            cur = 1.0
            for line in b.splitlines():
                if "Brightness:" in line:
                    try: cur = float(line.split(":")[1].strip())
                    except: pass
                    break
            nxt = min(1.0, round(cur + 0.1, 2))
            _run(["xrandr", "--output", name, "--brightness", str(nxt)])
            return f"Brightness {int(nxt*100)}%."
    return "No brightness control found (install brightnessctl)."


def _a_brightness_down(_: Dict[str, Any]) -> str:
    if shutil.which("brightnessctl"):
        _run(["brightnessctl", "set", "10%-"])
        return "Brightness down."
    if shutil.which("light"):
        _run(["light", "-U", "10"])
        return "Brightness down."
    if shutil.which("xrandr"):
        out = _run(["xrandr", "--verbose"])
        import re
        m = re.search(r"^\S+ connected", out, re.M)
        if m:
            name = m.group(0).split()[0]
            b = _run(["xrandr", "--verbose"])
            cur = 1.0
            for line in b.splitlines():
                if "Brightness:" in line:
                    try: cur = float(line.split(":")[1].strip())
                    except: pass
                    break
            nxt = max(0.3, round(cur - 0.1, 2))
            _run(["xrandr", "--output", name, "--brightness", str(nxt)])
            return f"Brightness {int(nxt*100)}%."
    return "No brightness control found."


def _a_system_stats(_: Dict[str, Any]) -> str:
    try:
        import psutil  # type: ignore
        cpu = psutil.cpu_percent(interval=0.4)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        batt = ""
        try:
            b = psutil.sensors_battery()
            if b: batt = f" Battery {int(b.percent)}%{' charging' if b.power_plugged else ''}."
        except: pass
        return f"CPU {cpu:.0f}%, RAM {mem.percent:.0f}% ({mem.used//1024//1024}M used), Disk {disk.percent:.0f}% full.{batt}"
    except ImportError:
        # fallback without psutil
        import os
        try:
            load = os.getloadavg()[0]
            return f"Load {load:.2f}. Install psutil for full stats: pip install psutil."
        except: return "System stats unavailable."
    except Exception as exc: return f"Stats failed: {exc}"


def _documents_dir() -> Path:
    d = Path.home() / "Documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _newest_note() -> Optional[Path]:
    """Most recently modified ``*.txt`` under ``~/Documents``, else None.

    No-name open resumes this (fix lane 2026-10-04); an empty/unreadable
    directory yields None so the caller falls back to a blank page. Read
    order only — never creates, never writes. Never raises.
    """
    try:
        docs = _documents_dir()
    except Exception:
        return None
    try:
        cands = [p for p in docs.glob("*.txt") if p.is_file()]
    except OSError:
        return None
    best: Optional[Path] = None
    best_mtime = -1.0
    for p in cands:
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > best_mtime:
            best_mtime = mt
            best = p
    return best


def _open_in_editor(path: Optional[Path] = None,
                    editor: str = "") -> str:
    """Open a document (or a blank page) in the LOCAL text editor.

    Default is the operating system's own ``.txt`` editor (Ubuntu Text
    Editor/gedit via ``xdg-open``, Notepad on Windows) — never an IDE.
    VS Code opens only when explicitly requested (``editor="vscode"``);
    when it is requested but missing, the default opens instead and the
    reply says so honestly. Never raises; failures are honest strings,
    and the caller audits. Returns the opened target description.
    """
    want_vscode = str(editor or "").strip().lower() == "vscode"
    if want_vscode and shutil.which("code"):
        target = str(path) if path is not None else str(Path.home())
        try:
            subprocess.Popen(["code", target], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return str(path) if path is not None else "a blank VS Code window"
        except Exception as exc:
            return f"could not open ({exc})".rstrip(".") + "."
    if want_vscode:
        note = "VS Code isn't installed, opened the default editor instead. "
    else:
        note = ""
    try:
        if sys.platform == "win32":
            if path is not None:
                os.startfile(str(path))  # noqa: S606 - operator's own file
            else:
                subprocess.Popen(["notepad"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            return note + (str(path) if path is not None else "a blank page")
        if path is not None:
            subprocess.Popen(["xdg-open", str(path)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return note + str(path)
        for binary in ("gnome-text-editor", "gedit", "kate", "mousepad",
                       "xed"):
            if shutil.which(binary):
                subprocess.Popen([binary], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return note + "a blank page"
        return (note + "no local text editor found — say 'create a text "
                "document' and I'll make a file instead.")
    except Exception as exc:
        return note + f"could not open ({exc})".rstrip(".") + "."


def _a_create_document(params: Dict[str, Any]) -> str:
    """'create (and open) a text document [called X]' -> make + open it.

    Scoped to ``~/Documents`` (bounded slug or timestamp name, ``.txt``),
    then opened in the editor so the operator can write immediately.
    Creation is reversible and audited; opening never executes content.
    """
    import datetime as _dt
    import re as _re
    raw = str(params.get("name") or "").strip()
    slug = _re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:40]
    if not slug:
        slug = "note-" + _dt.datetime.now().strftime("%Y%m%d-%H%M")
    content = str(params.get("content") or params.get("text") or "")
    target = _documents_dir() / f"{slug}.txt"
    try:
        if not target.exists():
            target.write_text(content, encoding="utf-8")
        elif content:
            # Named an existing note with dictated content: append, never
            # overwrite — the spoken words land in the file either way.
            with target.open("a", encoding="utf-8") as fh:
                fh.write(("\n" if target.stat().st_size else "") + content)
    except Exception as exc:
        return f"Could not create {target.name}: {exc}."
    editor = str(params.get("editor") or "")
    opened = _open_in_editor(target, editor)
    wrote = f" with {len(content)} chars written" if content else " (empty)"
    if opened.startswith("could not open"):
        return f"Created {target.name} in Documents{wrote}, but {opened}"
    if "isn't installed" in opened:
        return f"Created {target.name}{wrote}. {opened}"
    return f"Created {target.name}{wrote} and opened it in the editor."


#: Spoken site names -> canonical URLs (generic, operator-neutral).
_SITE_SHORTCUTS = {
    "youtube music": "https://music.youtube.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "messenger": "https://www.messenger.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "github": "https://github.com",
    "opencode": "https://opencode.ai",
    "opencode.ai": "https://opencode.ai",
    "qubit dashboard": "http://127.0.0.1:8890/",
    "qubit web dashboard": "http://127.0.0.1:8890/",
    "qubit": "http://127.0.0.1:8890/",
    "google": "https://www.google.com",
    "duckduckgo": "https://duckduckgo.com",
    "wikipedia": "https://www.wikipedia.org",
    "dexonline": "https://dexonline.ro",
}

_OPEN_VERBS = r"(?:open|launch|go to|visit|pop on|pull up|bring up)"

# ASR commonly writes "opencode" as "open code" (and may split the wake word:
# "Open, open code."). That shape used to reach open_app's bare "code" alias
# and launch VS Code. Keep explicit VS Code/editor phrasings on open_app.
_OPENCODE_RE = re.compile(
    r"\b(?:open|launch|go to|visit|pop on|pull up|bring up)\b"
    r"[\s,]+(?:the\s+)?(?:opencode|open\s*code|code)(?:\.ai)?\b",
    re.IGNORECASE)
_VSCODE_CONTEXT_RE = re.compile(
    r"\b(?:vs\s*code|visual\s+studio(?:\s+code)?|code\s+editor|"
    r"in\s+code|with\s+code)\b", re.IGNORECASE)


def _normalize_website_url(raw: str) -> str:
    """Spoken site reference -> https URL, or "" when unresolvable."""
    import re as _re
    t = (raw or "").strip().strip("\"'.,)")
    low = t.lower()
    for word in sorted(_SITE_SHORTCUTS, key=len, reverse=True):
        if _re.search(r"\b" + _re.escape(word) + r"\b", low):
            return _SITE_SHORTCUTS[word]
    # An explicit non-http(s) scheme is never upgraded to https — that
    # would launch something other than what was said.
    m_scheme = _re.search(r"\b([a-z][a-z0-9+.-]*)://", low)
    if m_scheme and m_scheme.group(1) not in ("http", "https"):
        return ""
    m = _re.search(r"(https?://\S+|www\.\S+|[a-z0-9-]+\.(?:com|ro|org|net|io|dev|app|ai|eu|uk|de|fr|md|gov|edu)\b\S*)", low)
    if not m:
        return ""
    url = m.group(1).strip("\"'.,)")
    if url.startswith("www."):
        url = "https://" + url
    elif "://" not in url:
        url = "https://" + url
    return url


def _a_open_website(params: Dict[str, Any]) -> str:
    """'open <site>' -> open the URL in the browser (read-only navigation).

    Only http(s) URLs ever launch; anything else (file://, javascript:,
    unresolvable names) is an honest refusal with zero side effects.
    """
    import urllib.parse as _up
    url = str(params.get("url") or "")
    try:
        parts = _up.urlparse(url)
    except Exception:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") \
            or "." not in (parts.netloc or ""):
        return ("I can't open that — give me a website address "
                "(like github.com) and I'll open it in the browser.")
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as exc:
        return f"Could not open {url}: {exc}."
    return f"Opened {url} in the browser."


def _a_open_document(params: Dict[str, Any]) -> str:
    """'open [the] text document [called X]' -> open it for writing.

    A named document is searched under ``~/Documents`` (exact then
    containing match, ``.txt`` assumed); a missing name is answered
    honestly, never guessed. No name resumes the most recently modified
    note (a bare "open a text file" is a resume-to-write intent), or a
    blank editor when no notes exist.
    """
    name = str(params.get("name") or "").strip().lower()
    editor = str(params.get("editor") or "")
    content = str(params.get("content") or params.get("text") or "")
    if name:
        docs = _documents_dir()
        cands = sorted(docs.glob("*.txt"))
        hit = next((p for p in cands if p.stem.lower() == name), None)
        if hit is None:
            hit = next((p for p in cands if name in p.stem.lower()), None)
        if hit is None:
            if content:
                # "open <name> and write <content>" for a missing note:
                # create it with the content rather than failing.
                return _a_create_document(
                    {"name": name, "content": content, "editor": editor})
            known = ", ".join(p.name for p in cands[:10]) or "no saved notes"
            return (f"I couldn't find {name!r} in Documents "
                    f"({known}). Say 'create a text document' to make one.")
        if content:
            # Dictated content for an existing note appends (never
            # overwrites) so "write X in it" lands in the file.
            try:
                with hit.open("a", encoding="utf-8") as fh:
                    fh.write(("\n" if hit.stat().st_size else "") + content)
            except Exception as exc:
                return f"Found {hit.name}, but could not write: {exc}."
            opened = _open_in_editor(hit, editor)
            if opened.startswith("could not open"):
                return (f"Added {len(content)} chars to {hit.name}, "
                        f"but {opened}")
            return (f"Added {len(content)} chars to {hit.name} "
                    f"and opened it in the editor.")
        opened = _open_in_editor(hit, editor)
    newest = _newest_note()
    if newest is not None:
        if content:
            try:
                with newest.open("a", encoding="utf-8") as fh:
                    fh.write(("\n" if newest.stat().st_size else "")
                             + content)
            except Exception as exc:
                return f"Found {newest.name}, but could not write: {exc}."
            opened = _open_in_editor(newest, editor)
            if opened.startswith("could not open"):
                return (f"Added {len(content)} chars to {newest.name}, "
                        f"but {opened}")
            return (f"Added {len(content)} chars to {newest.name} "
                    f"and opened it in the editor.")
        opened = _open_in_editor(newest, editor)
        if opened.startswith("could not open"):
            return f"Found {newest.name}, but {opened}"
        if "isn't installed" in opened:
            return f"Found {newest.name}. {opened}"
        return f"Opened {newest.name} in the editor (your most recent note)."
    if content:
        # No notes yet and dictated content: make the note with it.
        return _a_create_document(
            {"name": "", "content": content, "editor": editor})
    opened = _open_in_editor(None, editor)
    if opened.startswith("could not open"):
        return f"{opened.capitalize()}"
    if "isn't installed" in opened:
        return f"{opened.capitalize()}"
    return "Opened a blank page in the local text editor — say what to write."


#: Spoken folder names -> directory under $HOME opened in Files.
#: "downloads" alone implies the folder; "documents" needs an explicit
#: folder/directory word (a bare "open documents" stays an editor intent).
_FOLDERS = {"downloads": "Downloads", "documents": "Documents"}


def _strip_beneficiary_tail(name: str) -> str:
    """Remove a spoken beneficiary tail ("for me/for you/for us/please/now").

    Journal 2026-10-04: "Open a text file for me." extracted ``name="me"``
    (the ``for`` name-introducer matched the beneficiary) and the turn
    failed with "couldn't find 'me' in Documents". The beneficiary is never
    a filename: a trailing tail is dropped, and a bare beneficiary alone
    ("me", "for me", "me please") collapses to "" (no-name open resumes the
    newest note, else a blank page), never a Documents lookup. Never raises.
    """
    import re as _re
    t = (name or "").strip(" \"'.)").strip()
    if not t:
        return ""
    # Drop repeated trailing beneficiary phrases ("shopping list for me
    # please" -> "shopping list"; "me please" -> "me" -> "" below).
    prev = None
    while prev != t:
        prev = t
        t = _re.sub(
            r"\s+(?:for\s+(?:me|you|us)|please|now)\s*[.!?]*$", "", t,
            flags=_re.I).strip(" \"'.)").strip()
    # A bare beneficiary/pronoun alone is never a document name.
    if _re.fullmatch(
            r"(?:me|you|us|it|them|please|now|for\s+(?:me|you|us))", t,
            flags=_re.I):
        return ""
    return t


def _extract_doc_content(low: str) -> str:
    """Spoken document content ("write X in it", "with the text X", ...).

    Returns the dictated body or "" when the utterance carries none.
    Bounded (1000 chars): spoken content is short, and an unbounded grab
    would swallow trailing command words. Never raises.
    """
    import re as _re
    t = str(low or "")
    patterns = (
        r"\bwrite\s+(.+?)\s+in\s+it\b",
        r"\bwith\s+(?:the\s+)?(?:text|content)\s+(.+?)(?:\s+in\s+it)?\s*$",
        r"\bsaying\s+(.+?)(?:\s+in\s+it)?\s*$",
        r"\bcontaining\s+(.+?)(?:\s+in\s+it)?\s*$",
        r"\bthat\s+says\s+(.+?)(?:\s+in\s+it)?\s*$",
    )
    for pat in patterns:
        m = _re.search(pat, t)
        if not m:
            continue
        body = (m.group(1) or "").strip(" \"'.,!?").strip()
        if body and body.lower() not in ("it", "that", "this"):
            return body[:1000]
    return ""


def _a_open_folder(params: Dict[str, Any]) -> str:
    """'open downloads' / 'open documents folder' -> Files at that dir.

    Read-only navigation (no editor, no content touched). Never raises;
    failures are honest strings.
    """
    which = str((params or {}).get("folder") or "").strip().lower()
    if which not in _FOLDERS:
        return "Which folder should I open — Downloads or Documents?"
    label = _FOLDERS[which]
    path = Path.home() / label
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return f"{label} folder unavailable."
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - operator's own folder
        else:
            subprocess.Popen(["xdg-open", str(path)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception as exc:
        return f"Could not open {label}: {exc}."
    return f"Opened {label}."


# --- Slice 3 — VS Code edits by voice (default shape) -------------------------
# "in chat: in repo X, change ..." enters through Agent 1's route-word
# bypass (``chat_transport.parse_route_word``); the stripped task text lands
# here. The headless /chat worker applies free-form changes; these local
# actions cover the bounded slice: stage-or-apply a structured change
# (``repo_edit``, confirm-gated, no free writes), open VS Code on the file
# (``vscode_open``), and probe the official VS Code native MCP integration
# (``code_mcp_probe``, read-only — never installs, never invents a server).
# Dictated line-by-line edits are OUT (owner decision): they are refused
# with a describe-the-change pointer, never applied.

#: Phrases that mean "dictate code at me" (error-prone, OUT per Slice 3).
#: A bare "change the greeting" is a description (kept); two or more
#: "line N" claims, "type exactly", or "line by line" is dictation.
_DICTATION_LINE_RE = r"\bline\s+\d+\b"
_DICTATION_MARKERS = (
    "type exactly",
    "line by line",
    "line-by-line",
)


def _looks_like_dictation(instruction: str) -> bool:
    import re as _re
    low = (instruction or "").lower()
    if any(m in low for m in _DICTATION_MARKERS):
        return True
    return len(_re.findall(_DICTATION_LINE_RE, low)) >= 2


#: Repos an explicit absolute path may resolve under when no operator
#: project root matches: the operator's own tree and the scratch area.
#: Anything else is refused (never a drive-by absolute path by voice).
_SCRATCH_PREFIXES = ("/tmp",)


def _scratch_allowed(p: Path) -> bool:
    try:
        home = Path.home()
    except Exception:
        home = None
    for prefix in _SCRATCH_PREFIXES:
        try:
            if p.is_relative_to(prefix):
                return True
        except Exception:
            pass
    if home is not None:
        try:
            if p.is_relative_to(home):
                return True
        except Exception:
            pass
    return False


def _resolve_repo_dir(repo: str, path_hint: str = "") -> tuple:
    """Resolve a repo name/hint to a real directory, or (None, reply).

    Order: an explicit existing path (confined to the operator tree or
    scratch), then the operator-configured ``project_roots`` name/fuzzy
    match (same rule as ``_a_open_project``). Never raises; failures are
    honest strings and write nothing.
    """
    hint = (path_hint or "").strip()
    if hint:
        try:
            p = Path(os.path.expanduser(hint)).resolve()
        except Exception:
            return None, "I couldn't resolve that repo path."
        if not p.is_dir():
            return None, "That repo path isn't a directory I can open."
        if not _scratch_allowed(p):
            return None, (f"Refused: {p} is outside your projects and "
                           "scratch areas.")
        return p, ""
    name = (repo or "").strip().lower()
    roots = _project_roots()
    root = roots.get(name)
    if not root:
        for key, path in roots.items():
            if key in name and len(key) > 2:
                root = path
                break
    if not root:
        known = ", ".join(sorted(set(roots))) or "none configured"
        return None, (f"I don't know a repo called '{repo or 'that'}' "
                      f"({known}). Which repo should I use?")
    try:
        p = Path(os.path.expanduser(str(root))).resolve()
    except Exception:
        return None, "I couldn't resolve that repo path."
    if not p.is_dir():
        return None, "That repo path isn't a directory I can open."
    return p, ""


def _extract_repo_instruction(text: str, repo: str) -> str:
    """The change description with the "in repo X" span removed."""
    import re as _re
    instr = _re.sub(r"\bin\s+repo\s+[\w.-]+\s*,?", "", text,
                    count=1, flags=_re.I).strip(" ,.")
    return instr or text.strip()


def _extract_replace_parts(instruction: str) -> tuple:
    """Structured "replace A with B in F" -> (old, new, file), else Nones.

    Only this narrow, reviewable shape auto-applies on settle; free-form
    prose stages to the headless /chat worker instead.
    """
    import re as _re
    m = _re.search(r"\breplace\s+(.+?)\s+with\s+(.+?)"
                   r"(?:\s+in\s+(\S+))?\s*$", instruction, flags=_re.I)
    if not m:
        return None, None, None
    old, new = m.group(1).strip(" \"'."), m.group(2).strip(" \"'.")
    fil = (m.group(3) or "").strip(" \"'.,)")
    if not old or not new:
        return None, None, None
    return old, new, fil or ""


def _extract_path_token(text: str) -> str:
    """First path-like token (dotted filename or /path), else ""."""
    import re as _re
    m = _re.search(r"(/[\w.~/-]*[\w.-]+\.\w[\w.]*)", text)
    if m:
        return m.group(1)
    m = _re.search(r"\b([\w.-]+\.\w[\w.]*)\b", text)
    return m.group(1) if m else ""


def format_edit_report(stat: Dict[str, Any]) -> str:
    """Spoken diff summary: file + lines changed, never file content.

    ``stat`` carries counts only (``file``/``added``/``removed``/
    ``start_line``/``end_line``); any content-shaped key (``preview``,
    ``old``, ``new``) is ignored by construction so payload text can never
    reach the spoken path.
    """
    name = str((stat or {}).get("file") or "the file")
    try:
        added = int((stat or {}).get("added", 0))
    except Exception:
        added = 0
    try:
        removed = int((stat or {}).get("removed", 0))
    except Exception:
        removed = 0
    start = str((stat or {}).get("start_line") or "")
    end = str((stat or {}).get("end_line") or "")
    span = f" (lines {start}–{end})" if start and end else (
        f" (line {start})" if start else "")
    return (f"Changed {name}: {added} added, {removed} removed"
            f"{span}.")


def _a_repo_edit(params: Dict[str, Any]) -> str:
    """'in repo X, change ...' -> stage confirm-gated, apply on settle.

    Staging writes nothing (the reply names the voice-"yes" settle word).
    The settled apply handles only the structured "replace A with B in F"
    shape: single file, confined to the repo, exact-once match (fail
    closed), bounded sizes. Free-form prose stages to the headless /chat
    worker instead of being guessed at; dictation is refused outright.
    """
    repo = str((params or {}).get("repo") or "")
    instruction = str((params or {}).get("instruction") or "")
    confirmed = str((params or {}).get("confirmed") or "").strip().lower()
    root, reply = _resolve_repo_dir(repo, str((params or {}).get("path") or ""))
    if root is None:
        return reply
    if _looks_like_dictation(instruction):
        return ("I don't take dictated line-by-line edits — describe the "
                f"change ('change the greeting in {repo or 'the repo'}') "
                "and I'll run it in chat.")
    target_desc = str((params or {}).get("file") or "") or "the code"
    if confirmed != "yes":
        short = instruction[:160] or "that change"
        return (f"Ready to change {target_desc} in {repo or root.name} "
                f"({short}). Say yes to apply it.")
    old = str((params or {}).get("old") or "")
    new = str((params or {}).get("new") or "")
    fil = str((params or {}).get("file") or "")
    if not old or not new or not fil:
        return (f"I'll run that in chat against {repo or root.name}: "
                f"'{instruction[:160]}'. Say yes to launch the worker.")
    if len(new) > 10 * 1024:
        return "That replacement is too large to apply by voice."
    target = (root / fil).resolve()
    try:
        inside = target.is_relative_to(root)
    except Exception:
        inside = False
    if not inside:
        return f"Refused: {fil} is outside the {repo or root.name} repo."
    if not target.is_file():
        return f"I couldn't find {fil} in {repo or root.name}."
    try:
        if target.stat().st_size > 1024 * 1024:
            return f"{fil} is too large to patch by voice."
        body = target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Could not read {fil}: {exc}."
    count = body.count(old)
    if count == 0:
        return f"I couldn't find that text in {fil} — nothing changed."
    if count > 1:
        return (f"'{old[:60]}' appears more than once in {fil} — "
                "say which line to change.")
    lineno = body[:body.index(old)].count("\n") + 1
    try:
        target.write_text(body.replace(old, new, 1), encoding="utf-8")
    except Exception as exc:
        return f"Could not write {fil}: {exc}."
    log_event("local.repo_edit", repo=repo[:80], file=fil[:120],
              line=lineno)
    return format_edit_report({"file": fil, "added": 1, "removed": 1,
                               "start_line": lineno, "end_line": lineno})


def _a_vscode_open(params: Dict[str, Any]) -> str:
    """Open a file (or repo) in VS Code at an optional line.

    ``code -g path[:line]`` — navigation only, never a write. Honest when
    ``code`` is absent; never raises.
    """
    line = str((params or {}).get("line") or "").strip()
    if line and not line.isdigit():
        line = ""
    target_hint = str((params or {}).get("path") or "").strip()
    if not target_hint and (params or {}).get("repo"):
        root, reply = _resolve_repo_dir(str((params or {}).get("repo") or ""))
        if root is None:
            return reply
        target = root
    elif target_hint:
        try:
            target = Path(os.path.expanduser(target_hint)).resolve()
        except Exception:
            return "I couldn't resolve that path."
        if not target.exists():
            return "I couldn't find that file to open."
        if not _scratch_allowed(target):
            return (f"Refused: {target} is outside your projects and "
                    "scratch areas.")
    else:
        return "Which file should I open in VS Code?"
    if not shutil.which("code"):
        return ("VS Code isn't installed (no `code` on PATH), so I can't "
                "open it — the file is untouched.")
    goto = f"{target}:{line}" if line and target.is_file() else str(target)
    try:
        subprocess.Popen(["code", "-g", goto],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as exc:
        return f"Could not open VS Code: {exc}."
    name = target.name or str(target)
    at = f" at line {line}" if line and target.is_file() else ""
    return f"Opened {name}{at} in VS Code."


def _a_code_mcp_probe(_: Dict[str, Any]) -> str:
    """Read-only probe of the official VS Code native MCP integration.

    Checks ``code --add-mcp`` presence via the CLI help text. Reports the
    upgrade path honestly (present -> live editor state usable; absent ->
    CLI-open + worker-edit fallback). Never launches, installs, or
    configures anything, and never invents an MCP server.
    """
    if not shutil.which("code"):
        return ("VS Code isn't installed here — falling back to CLI-open "
                "+ worker-edit.")
    try:
        help_text = _run(["code", "--help"], timeout=10.0)
    except Exception:
        return ("I couldn't probe the VS Code CLI — falling back to "
                "CLI-open + worker-edit.")
    if "--add-mcp" in (help_text or ""):
        return ("The VS Code native MCP integration is available "
                "(code --add-mcp present) — the worker can use live "
                "editor state.")
    return ("No native MCP flag on this VS Code CLI — falling back to "
            "CLI-open + worker-edit.")


def _a_quick_note(params: Dict[str, Any]) -> str:
    text = params.get("note", "").strip()
    if not text:
        return "What should I note down?"
    from pathlib import Path; import datetime
    p = Path.home() / ".config" / "qubit-voicebridge" / "quick-notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(p, "a") as f: f.write(f"\n- [{ts}] {text}\n")
    log_event("local.quick_note", text=text[:120])
    return f"Noted: {text[:80]}"


# --- Durable one-shot timers ---------------------------------------------
# Journal-proof defect (2026-10-03, lane 1): a 1-minute voice timer spoke
# "I'll notify you" and never fired. The old `_a_timer` armed only an
# in-process `threading.Timer` (daemon-inheriting: it dies with the worker
# thread/helper process that set it), wrote ZERO audit events, and ignored
# notify-send/paplay failures silently — so a miss was invisible.
#
# New contract: setting a timer persists a ledger record synchronously
# (`local_timers.json` next to the schedules store, atomic replace, fcntl
# claim so exactly one firer wins), then arms a DETACHED survivor
# (systemd-run transient `--on-active`, else a reparented sleeper) that
# re-enters this module (`--fire-timer <id>`) from a fresh process. The
# in-process thread is kept as a best-effort second layer. Every hop is
# audited (`local.timer.scheduled/armed/fired/notify_failed/arm_failed`);
# notify/sound failures are recorded LOUD on the record, never swallowed.
#
# Deliberately NOT used: `schedules.create_job(once in N minutes)` — the
# only autonomous ticker is the hourly communications-monitor `autofire`
# pass, so a schedule-backed 1-minute timer would fire up to ~60 min late.
# The ledger survives reboots honestly (a `scheduled` record past its
# fire_at with no `fired` receipt means "never fired" — askable via
# `timer_status`/`poll_fired_timers`, the pull seam for the voice lane).
_TIMER_LEDGER_NAME = "local_timers.json"
_TIMER_MIN, _TIMER_MAX = 1, 180
# Bug-1 lane (2026-10-04): the voice reminder dedup lived only in the
# helper's in-memory `_timers_announced` set while the ledger keeps
# `status=fired` forever — so every helper restart re-nagged (journal:
# f432b3b9cde7 reminded once per helper generation). The ledger now
# carries a persistent `announced` bit, and `poll_fired_timers` consumes
# what it returns (atomic under the ledger lock). `_TIMER_DELIVERY_TTL_S`
# is the backstop for pre-fix rows that were announced in-memory but never
# marked (fired long ago -> never re-pulled, so the deploy restart itself
# stays silent). `_TIMER_DELIVERY_PER_POLL` mirrors
# `DesktopHelper._TIMER_DELIVERY_PER_TURN` exactly: the helper drains at
# most that many per turn, so the poller marks at most that many per call
# and a 5-deep backlog drains 3 + 2 instead of losing rows. Keep the two
# constants in lock-step; the coupling is pinned by
# `tests/test_timer_announced_persist9u.py::test_drain_contract_preserved`
# and `tests/test_timer_reminder9u.py::test_delivery_capped_per_turn`.
_TIMER_DELIVERY_TTL_S = 6 * 3600.0
_TIMER_FIRED_PRUNE_S = 24 * 3600.0
_TIMER_DELIVERY_PER_POLL = 3


def _timer_state_dir() -> Path:
    """Durable dir for the timer ledger (hermetic seam: env override)."""
    over = os.environ.get("QUBIT_TIMER_STATE_DIR")
    if over:
        p = Path(over)
        p.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from ..config import Config
        sd = Path(Config.load().state_dir())
        sd.mkdir(parents=True, exist_ok=True)
        return sd
    except Exception:
        from ..config import canonical_state_dir
        sd = Path(canonical_state_dir())
        sd.mkdir(parents=True, exist_ok=True)
        return sd


def _timer_ledger_path() -> Path:
    return _timer_state_dir() / _TIMER_LEDGER_NAME


def _timer_lock(path: Path):
    """Best-effort exclusive file lock (POSIX; no-op where unavailable)."""
    import contextlib

    @contextlib.contextmanager
    def _locked():
        try:
            import fcntl
            fh = open(str(path) + ".lock", "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                except Exception:
                    pass
                fh.close()
        except Exception:
            yield
    return _locked()


def _timer_read_all() -> Dict[str, Any]:
    import json as _j
    p = _timer_ledger_path()
    try:
        data = _j.loads(p.read_text())
        if isinstance(data, dict) and isinstance(data.get("timers"), list):
            return data
    except Exception:
        pass
    return {"timers": []}


def _timer_write_all(data: Dict[str, Any]) -> None:
    import json as _j
    p = _timer_ledger_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(_j.dumps(data, indent=2, sort_keys=True))
    os.replace(str(tmp), str(p))


def schedule_timer(mins: int) -> Dict[str, Any]:
    """Persist a `scheduled` timer record; returns the record (sync)."""
    import time as _t
    import uuid as _u
    mins = max(_TIMER_MIN, min(int(mins), _TIMER_MAX))
    now = _t.time()
    rec = {"id": _u.uuid4().hex[:12], "mins": mins,
           "created_at": now, "fire_at": now + mins * 60.0,
           "status": "scheduled", "fired_at": 0.0,
           "mechanism": "", "notify": {}, "sound": {}}
    with _timer_lock(_timer_ledger_path()):
        data = _timer_read_all()
        data["timers"].append(rec)
        _timer_write_all(data)
    return dict(rec)


def timer_status(timer_id: str) -> Optional[Dict[str, Any]]:
    """Read one timer record (None when unknown). Never raises."""
    try:
        for rec in _timer_read_all().get("timers", []):
            if str(rec.get("id")) == str(timer_id):
                return dict(rec)
    except Exception:
        pass
    return None


def poll_fired_timers(since: float = 0.0, *, limit: int = _TIMER_DELIVERY_PER_POLL,
                      consume: bool = True) -> List[Dict[str, Any]]:
    """Oldest-first `fired` records with fired_at > since (voice pull seam).

    A future service/helper turn can announce these ("your timer went off
    while you were away") without any push channel into those processes.

    Bug-1 lane (2026-10-04): this is now CONSUMING by default. Rows already
    marked `announced` are skipped, rows older than `_TIMER_DELIVERY_TTL_S`
    are never returned (stale pre-fix rows must not re-nag after a
    restart), at most `limit` rows are returned oldest-first, and returned
    rows are atomically marked `announced` under the ledger lock — so no
    restart/reboot ever re-nags, while a backlog still drains across turns
    (3 + 2, mirroring the helper's per-turn cap) instead of being lost.
    Pass `consume=False` for a pure read (nothing is marked). `since`,
    `limit`, and `consume` are keyword-compatible with the old
    positional-`since` call shape. Never raises.
    """
    import time as _t
    now = _t.time()
    try:
        lim = max(1, int(limit))
    except Exception:
        lim = _TIMER_DELIVERY_PER_POLL
    out: List[Dict[str, Any]] = []
    try:
        with _timer_lock(_timer_ledger_path()):
            data = _timer_read_all()
            cand = []
            for rec in data.get("timers", []):
                if not isinstance(rec, dict):
                    continue
                if rec.get("status") != "fired":
                    continue
                if rec.get("announced") is True:
                    continue
                try:
                    fired_at = float(rec.get("fired_at") or 0.0)
                except Exception:
                    continue
                if fired_at <= float(since):
                    continue
                if fired_at <= 0.0 or (now - fired_at) > _TIMER_DELIVERY_TTL_S:
                    continue
                cand.append(rec)
            cand.sort(key=lambda r: float(r.get("fired_at") or 0.0))
            picked = cand[:lim]
            if consume and picked:
                for rec in picked:
                    rec["announced"] = True
                    rec["announced_at"] = now
                _timer_write_all(data)
                for rec in picked:
                    log_event("local.timer.announced",
                              timer_id=str(rec.get("id"))[:16])
            out = [dict(rec) for rec in picked]
    except Exception:
        pass
    try:
        prune_old_fired()
    except Exception:
        pass
    return out


def mark_timers_announced(timer_ids) -> int:
    """Persistently mark fired rows announced (explicit ack seam).

    `poll_fired_timers` already consumes what it returns; this is the
    explicit form (tests, future callers). Returns the count marked.
    Never raises.
    """
    import time as _t
    try:
        want = {str(t) for t in (timer_ids or [])}
    except Exception:
        return 0
    if not want:
        return 0
    marked = 0
    try:
        with _timer_lock(_timer_ledger_path()):
            data = _timer_read_all()
            for rec in data.get("timers", []):
                if (isinstance(rec, dict)
                        and str(rec.get("id")) in want
                        and rec.get("status") == "fired"
                        and rec.get("announced") is not True):
                    rec["announced"] = True
                    rec["announced_at"] = _t.time()
                    marked += 1
            if marked:
                _timer_write_all(data)
    except Exception:
        return 0
    return marked


def prune_old_fired(max_age_s: float = _TIMER_FIRED_PRUNE_S) -> int:
    """Delete `fired` rows older than `max_age_s` (ledger hygiene).

    The delivery TTL suppresses stale re-nags; this reaps the rows so the
    ledger does not grow forever (Lane 2's flagged open issue #1).
    `scheduled`/`firing` rows are never touched. Returns the count
    pruned. Never raises.
    """
    import time as _t
    try:
        max_age = float(max_age_s)
    except Exception:
        return 0
    now = _t.time()
    pruned = 0
    try:
        with _timer_lock(_timer_ledger_path()):
            data = _timer_read_all()
            rows = data.get("timers", [])
            keep = []
            for rec in rows:
                if (isinstance(rec, dict) and rec.get("status") == "fired"):
                    try:
                        age = now - float(rec.get("fired_at") or 0.0)
                    except Exception:
                        age = max_age + 1.0
                    if age > max_age:
                        pruned += 1
                        log_event("local.timer.pruned",
                                  timer_id=str(rec.get("id"))[:16],
                                  age_h=round(age / 3600.0, 1))
                        continue
                keep.append(rec)
            if pruned:
                data["timers"] = keep
                _timer_write_all(data)
    except Exception:
        return 0
    return pruned


def claim_timer(timer_id: str) -> Optional[Dict[str, Any]]:
    """Atomically claim a `scheduled` timer (scheduled->firing).

    Exactly one caller wins; every later claim returns None. Never raises.
    """
    import time as _t
    try:
        with _timer_lock(_timer_ledger_path()):
            data = _timer_read_all()
            for rec in data.get("timers", []):
                if (str(rec.get("id")) == str(timer_id)
                        and rec.get("status") == "scheduled"):
                    rec["status"] = "firing"
                    rec["claimed_at"] = _t.time()
                    _timer_write_all(data)
                    return dict(rec)
    except Exception as exc:
        log_event("local.timer.claim_failed", timer_id=str(timer_id)[:16],
                  error=str(exc)[:160])
    return None


def reap_stale_firing(max_age_s: float = 300.0) -> int:
    """Return crashed `firing` claims to `scheduled` (audited, bounded).

    A firer killed between claim and finalize (SIGKILL, power loss) would
    otherwise leave the timer silent forever. Firing takes milliseconds
    (notify-send + paplay + one ledger write), so a claim older than
    `max_age_s` is a crashed firer, not a slow one. Never raises.
    """
    import time as _t
    now = _t.time()
    reaped = 0
    try:
        with _timer_lock(_timer_ledger_path()):
            data = _timer_read_all()
            for rec in data.get("timers", []):
                if rec.get("status") != "firing":
                    continue
                try:
                    age = now - float(rec.get("claimed_at") or 0.0)
                except Exception:
                    age = max_age_s + 1.0
                if age >= max_age_s:
                    rec["status"] = "scheduled"
                    rec["reaped"] = int(rec.get("reaped") or 0) + 1
                    reaped += 1
                    log_event("local.timer.reaped",
                              timer_id=str(rec.get("id"))[:16],
                              age_s=round(age, 1))
            if reaped:
                _timer_write_all(data)
    except Exception as exc:
        log_event("local.timer.reap_failed", error=str(exc)[:160])
    return reaped


def _timer_notify(title: str, body: str) -> Dict[str, Any]:
    """Desktop notification with a typed receipt (never silent)."""
    if not shutil.which("notify-send"):
        return {"ok": False, "detail": "notify-send is not installed"}
    try:
        proc = subprocess.run(["notify-send", "-a", "Qubit", title, body],
                              capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {"ok": False, "detail": f"notify-send failed: {exc}"[:200]}
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[:160]
        return {"ok": False,
                "detail": f"notify-send exited {proc.returncode}: {err}"}
    return {"ok": True, "code": 0}


def _timer_sound() -> Dict[str, Any]:
    """Alarm sound with a typed receipt (never silent)."""
    cand = Path("/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga")
    if not shutil.which("paplay"):
        return {"ok": False, "detail": "paplay is not installed"}
    if not cand.is_file():
        return {"ok": False, "detail": f"alarm file missing: {cand}"}
    try:
        proc = subprocess.run(["paplay", str(cand)], capture_output=True,
                              text=True, timeout=10)
    except Exception as exc:
        return {"ok": False, "detail": f"paplay failed: {exc}"[:200]}
    if proc.returncode != 0:
        return {"ok": False,
                "detail": f"paplay exited {proc.returncode}"}
    return {"ok": True, "detail": "alarm played"}


def fire_timer(timer_id: str, notify_fn: Optional[Callable] = None,
               sound_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Fire one timer exactly once: claim, notify+sound LOUD, finalize.

    Writes only the timer ledger (never chat/session state). Returns
    `{"fired": bool, ...}`; `fired=False` means another layer already won
    the claim (not an error). Idempotent and safe to call from any layer.
    """
    import time as _t
    rec = claim_timer(timer_id)
    if rec is None:
        return {"fired": False, "detail": "already claimed/fired/unknown"}
    mins = int(rec.get("mins") or 0)
    plural = "s" if mins != 1 else ""
    title = f"Timer: {mins} minute{plural} up."
    notify = (notify_fn or _timer_notify)(title, "Time's up.")
    try:
        sound = (sound_fn or _timer_sound)()
    except Exception as exc:
        sound = {"ok": False, "detail": f"sound failed: {exc}"[:200]}
    if not isinstance(sound, dict):
        sound = {"ok": False, "detail": f"sound returned {sound!r}"[:160]}
    if not isinstance(notify, dict):
        notify = {"ok": False, "detail": f"notify returned {notify!r}"[:160]}
    log_event("local.timer.fired", timer_id=str(timer_id)[:16], mins=mins,
              notify_ok=bool(notify.get("ok")),
              notify_detail=str(notify.get("detail") or "")[:160],
              sound_ok=bool(sound.get("ok")))
    if not notify.get("ok"):
        log_event("local.timer.notify_failed", timer_id=str(timer_id)[:16],
                  detail=str(notify.get("detail") or "")[:200])
    try:
        with _timer_lock(_timer_ledger_path()):
            data = _timer_read_all()
            for row in data.get("timers", []):
                if str(row.get("id")) == str(timer_id):
                    row["status"] = "fired"
                    row["fired_at"] = _t.time()
                    row["notify"] = notify
                    row["sound"] = sound
                    break
            _timer_write_all(data)
    except Exception as exc:
        log_event("local.timer.finalize_failed",
                  timer_id=str(timer_id)[:16], error=str(exc)[:160])
        return {"fired": True, "detail": "fired but receipt write failed",
                "notify": notify, "sound": sound}
    return {"fired": True, "notify": notify, "sound": sound}


def _arm_timer_survivor(rec: Dict[str, Any]) -> tuple:
    """Spawn a detached process that fires the timer on time.

    Preferred: `systemd-run --user --on-active=<s>s` transient timer (owned
    by the user manager, survives the setting process AND its children).
    Fallback: a reparented `python -m voicebridge.local.actions
    --fire-timer-in <s> <id>` sleeper (double-forked via start_new_session,
    stdio to DEVNULL). Raises when neither can be armed — the caller must
    degrade LOUD, never promise a notification it cannot deliver.
    """
    import time as _t
    delay = max(1, int(round(float(rec["fire_at"]) - _t.time())))
    py = sys.executable or "python3"
    argv = [py, "-m", "voicebridge.local.actions",
            "--fire-timer", str(rec["id"])]
    if shutil.which("systemd-run"):
        cmd = (["systemd-run", "--user", f"--on-active={delay}",
                f"--unit=qubit-timer-{rec['id']}",
                "--timer-property=AccuracySec=1s",
                "--description=Qubit voice timer"])
        # Hermetic seam: a scratch ledger dir must reach the survivor.
        if os.environ.get("QUBIT_TIMER_STATE_DIR"):
            cmd.append("--setenv=QUBIT_TIMER_STATE_DIR="
                       + os.environ["QUBIT_TIMER_STATE_DIR"])
        cmd += argv
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=15)
        except Exception as exc:
            raise RuntimeError(f"systemd-run failed: {exc}"[:200])
        if proc.returncode != 0:
            err = ((proc.stderr or "") + (proc.stdout or "")) \
                .strip()[:200]
            raise RuntimeError(f"systemd-run exited {proc.returncode}: "
                               f"{err}")
        return ("systemd-run", f"on-active={delay}s")
    sleeper = [py, "-m", "voicebridge.local.actions",
               "--fire-timer-in", str(delay), str(rec["id"])]
    try:
        subprocess.Popen(
            sleeper, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
    except Exception as exc:
        raise RuntimeError(f"detached sleeper failed: {exc}"[:200])
    return ("detached-sleep", f"in={delay}s")


def _timer_clock(fire_at: float) -> str:
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(float(fire_at)).strftime("%H:%M")
    except Exception:
        return "on time"


def _a_timer(params: Dict[str, Any]) -> str:
    mins = params.get("minutes", 5)
    try:
        mins = int(mins)
    except Exception:
        mins = 5
    mins = max(_TIMER_MIN, min(mins, _TIMER_MAX))
    try:
        reaped = reap_stale_firing()
        if reaped:
            log_event("local.timer.reaped_at_set", count=reaped)
        pruned = prune_old_fired()
        if pruned:
            log_event("local.timer.pruned_at_set", count=pruned)
    except Exception:
        pass
    try:
        rec = schedule_timer(mins)
    except Exception as exc:
        log_event("local.timer.persist_failed", error=str(exc)[:160])
        return (f"I couldn't save the timer — it isn't set ({exc}). "
                f"Nothing will notify you.")
    log_event("local.timer.scheduled", timer_id=rec["id"], mins=mins,
              fire_at=rec["fire_at"])
    # Layer 1 (timely, survives this thread/process): detached survivor.
    # Layer 2 (best-effort, this process only): in-process fallback.
    # Both converge on claim_timer(); exactly one of them fires.
    try:
        mechanism, _detail = _arm_timer_survivor(rec)
        log_event("local.timer.armed", timer_id=rec["id"],
                  mechanism=mechanism)
        durable = True
    except Exception as exc:
        log_event("local.timer.arm_failed", timer_id=rec["id"],
                  error=str(exc)[:200])
        durable = False
    try:
        import threading as _th
        _th.Timer(mins * 60, fire_timer, args=(rec["id"],)).start()
    except Exception as exc:
        log_event("local.timer.thread_failed", timer_id=rec["id"],
                  error=str(exc)[:160])
    when = _timer_clock(rec["fire_at"])
    plural = "s" if mins != 1 else ""
    if durable:
        return (f"Timer set for {mins} minute{plural}. "
                f"I'll notify you even if I restart (fires ~{when}).")
    return (f"Timer set for {mins} minute{plural} — warning: I could only "
            f"arm it in this process, so a restart would lose it "
            f"(fires ~{when}).")


#: Margin kept between a moved window and the display edges (pixels).
_DISPLAY_MARGIN = 12


def _query_displays() -> List[Any]:
    """Connected outputs (orb monitor parser, fail-closed to [])."""
    try:
        from ..orb.monitors import query_outputs
        return list(query_outputs() or [])
    except Exception:
        return []


def _display_run(args: List[str], timeout: float = 10.0) -> Any:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _display_ordinal(text: str) -> int:
    """Target display number from 'first display' / 'display 2'. 0 = none."""
    low = (text or "").lower()
    hits: List[tuple] = []
    words = {"first": 1, "1st": 1, "primary": 1, "main": 1,
             "second": 2, "2nd": 2, "third": 3, "3rd": 3}
    for m in re.finditer(r"\b(first|1st|primary|main|second|2nd|third|3rd)\b", low):
        hits.append((m.start(), words[m.group(1)]))
    for m in re.finditer(r"\b(?:display|monitor|screen)\s*(?:number\s*)?(\d+)\b", low):
        hits.append((m.start(), int(m.group(1))))
    for m in re.finditer(r"\b(\d+)(?:st|nd|rd|th)?\s+(?:display|monitor|screen)\b", low):
        hits.append((m.start(), int(m.group(1))))
    return max(hits)[1] if hits else 0


def _pick_display(outputs: List[Any], which: int) -> Any:
    """Display 1 is the primary (else leftmost); 2+ are the others by x."""
    try:
        ordered = sorted(outputs, key=lambda o: (int(o.x), int(o.y)))
    except Exception:
        return None
    if not ordered:
        return None
    primary = next((o for o in ordered if bool(getattr(o, "primary", False))), None)
    if which == 1:
        return primary or ordered[0]
    others = [o for o in ordered if o is not primary] if primary else ordered[1:]
    idx = which - 2
    return others[idx] if 0 <= idx < len(others) else None


def _display_name(which: int) -> str:
    return {1: "first", 2: "second", 3: "third"}.get(which, f"number {which}")


def _a_display_switch(params: Dict[str, Any]) -> str:
    """Move the ACTIVE window to the requested physical display.

    Safe and reversible: it only repositions/resizes the focused window and
    re-activates it. Fullscreen and maximized state are preserved (removed for
    the move, restored afterwards). No application state is touched.
    """
    which = int((params or {}).get("display") or 0)
    if which <= 0:
        return "Which display — the first or the second?"
    if not os.environ.get("DISPLAY"):
        return "I need an X11 display to move windows between screens."
    if not shutil.which("wmctrl") or not shutil.which("xdotool"):
        return "I need wmctrl and xdotool to move windows between screens."
    outputs = _query_displays()
    if not outputs:
        return "I couldn't read the display layout."
    target = _pick_display(outputs, which)
    if target is None:
        if len(outputs) <= 1:
            return "I only see one display connected."
        return f"I don't see a {_display_name(which)} display connected."
    try:
        win = _display_run(["xdotool", "getactivewindow"]).stdout.strip()
    except Exception as exc:
        return f"I couldn't find the active window: {exc}"
    if not win:
        return "I couldn't find the active window."
    try:
        win_id = int(win)
    except ValueError:
        return "I couldn't read the active window id."
    win_hex = hex(win_id)
    try:
        geo = _display_run(
            ["xdotool", "getwindowgeometry", "--shell", win]).stdout
    except Exception:
        geo = ""
    dims: Dict[str, int] = {}
    for line in geo.splitlines():
        key, sep, val = line.partition("=")
        if sep and val.strip().lstrip("-").isdigit():
            dims[key.strip()] = int(val.strip())
    max_w = max(320, int(target.w) - 2 * _DISPLAY_MARGIN)
    max_h = max(240, int(target.h) - 2 * _DISPLAY_MARGIN)
    width = min(dims.get("WIDTH") or max_w, max_w)
    height = min(dims.get("HEIGHT") or max_h, max_h)
    x = int(target.x) + _DISPLAY_MARGIN
    y = int(target.y) + _DISPLAY_MARGIN
    try:
        state = _display_run(
            ["xprop", "-id", win_hex, "_NET_WM_STATE"]).stdout
    except Exception:
        state = ""
    was_fullscreen = "FULLSCREEN" in state
    was_max_v = "_NET_WM_STATE_MAXIMIZED_VERT" in state
    was_max_h = "_NET_WM_STATE_MAXIMIZED_HORZ" in state
    remove = ["fullscreen"] if was_fullscreen else []
    if was_max_v:
        remove.append("maximized_vert")
    if was_max_h:
        remove.append("maximized_horz")
    try:
        if remove:
            _display_run(["wmctrl", "-i", "-r", win_hex,
                          "-b", "remove," + ",".join(remove)])
            # The WM applies state changes asynchronously; moving too early
            # lets a maximized/fullscreen window snap back to the old output.
            time.sleep(0.15)
        moved = _display_run(
            ["wmctrl", "-i", "-r", win_hex, "-e",
             f"0,{x},{y},{width},{height}"])
        if moved.returncode != 0:
            detail = (moved.stderr or moved.stdout or "wmctrl failed").strip()
            return f"I couldn't move the window: {detail[:160]}"
        restore = ["fullscreen"] if was_fullscreen else []
        if not was_fullscreen and was_max_v:
            restore.append("maximized_vert")
        if not was_fullscreen and was_max_h:
            restore.append("maximized_horz")
        if restore:
            _display_run(["wmctrl", "-i", "-r", win_hex,
                          "-b", "add," + ",".join(restore)])
        _display_run(["wmctrl", "-i", "-a", win_hex])
    except Exception as exc:
        return f"I couldn't move the window: {exc}"
    return f"Switched to the {_display_name(which)} display."


def _a_window_close(_: Dict[str, Any]) -> str:
    if shutil.which("xdotool"):
        _run(["xdotool", "key", "Alt+F4"])
        return "Closing window."
    # wayland fallback: wtype
    if shutil.which("wtype"):
        return "Close the window with Alt+F4."
    return "Install xdotool to close windows by voice."


def _a_find_file(params: Dict[str, Any]) -> str:
    q = params.get("query", "").strip()
    if not q: return "What file should I find?"
    # quick locate in home (bounded)
    from pathlib import Path
    import subprocess as sp
    try:
        out = sp.run(["find", str(Path.home()), "-maxdepth", "4", "-iname", f"*{q}*", "-type", "f"],
                     capture_output=True, text=True, timeout=6).stdout.strip()
        if not out: return f"No file matching '{q}' in the top levels of home."
        lines = out.splitlines()[:5]
        return "Found: " + "; ".join(lines) + (f" and {len(out.splitlines())-5} more." if len(out.splitlines())>5 else "")
    except Exception as exc: return f"Search failed: {exc}"


def _a_empty_trash(_: Dict[str, Any]) -> str:
    trash = __import__("pathlib").Path.home() / ".local/share/Trash/files"
    if trash.exists():
        import shutil as sh
        count = len(list(trash.iterdir()))
        if count == 0: return "Trash is already empty."
        sh.rmtree(trash, ignore_errors=True)
        trash.mkdir(parents=True, exist_ok=True)
        (trash.parent / "info").mkdir(parents=True, exist_ok=True)
        return f"Emptied {count} item{'s' if count!=1 else ''} from trash."
    return "Trash is already empty."



def _a_time_date(_: Dict[str, Any]) -> str:
    import datetime
    # Use Europe/Bucharest
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Bucharest")
        now = datetime.datetime.now(tz)
    except Exception:
        now = datetime.datetime.now()
    # Natural: "Today is Saturday, August 23. The time is 02:14 EEST."
    day = now.strftime("%A, %B %d")
    time_str = now.strftime("%H:%M %Z").strip()
    if time_str.endswith(" "):
        time_str = now.strftime("%H:%M")
        time_str += " EEST"  # fallback known zone
    return f"Today is {day}. The time is {time_str}."

def _a_bluetooth_toggle(_: Dict[str, Any]) -> str:
    if shutil.which("bluetoothctl"):
        cur = _run(["bluetoothctl", "show"])
        on = "Powered: yes" in cur
        _run(["bluetoothctl", "power", "off" if on else "on"])
        return f"Bluetooth {'off' if on else 'on'}."
    if shutil.which("rfkill"):
        _run(["rfkill", "toggle", "bluetooth"])
        return "Bluetooth toggled."
    return "Bluetooth control not found."


# --- Browser tabs (voice over the loopback CDP authority) -------------------
# All browser effects go through voicebridge.browser_tabs (loopback CDP only;
# personal profiles never touched). Mutations pass confirmed=True because the
# voice task path already said yes before the turn runs. Replies state what
# happened (opened/closed/duplicated/switched + tab title), never phantom work.
_TAB_UNREACHABLE_CODES = frozenset({"CDP_UNAVAILABLE", "BROWSER_UNAVAILABLE"})
_TABS_UNREACHABLE = ("The browser isn't reachable right now — open Chromium "
                     "with remote debugging on, then try again.")


def _tabs_cfg():
    """Live config for the tab handlers (lazy: keeps this module import-light).

    Never imports service/desktop_helper: Config.load() is the same loader
    every backend surface uses.
    """
    try:
        from ..config import Config
        return Config.load()
    except Exception as exc:
        log_event("local.tabs.config_error", error=str(exc)[:160])
        return None


def _tab_label(page: Dict[str, Any], limit: int = 60) -> str:
    title = str((page or {}).get("title") or "").strip()
    if title:
        return title[:limit]
    url = str((page or {}).get("url") or "")
    if not url or "about:blank" in url:
        return "blank tab"
    return url[:limit]


def _tabs_resolve(name: str):
    """Resolve a tab via the authority's fuzzy matcher (ledger current-tab +
    ambiguity fail-closed). Returns (cfg, page) or (None, reply-on-failure)."""
    from .. import browser_tabs as _bt
    cfg = _tabs_cfg()
    if cfg is None:
        return None, "Browser control isn't configured on this machine."
    try:
        return cfg, _bt._resolve_target(cfg, name)
    except _bt.BrowserTabsError as exc:
        if exc.code in _TAB_UNREACHABLE_CODES:
            return None, _TABS_UNREACHABLE
        if exc.code == _bt.BAD_INPUT_CODE:
            # Ambiguity fail-closed: the detail already lists candidates.
            return None, f"Several tabs match — {exc.detail[:200]} Which one?"
        if name:
            return None, f"I couldn't find an open tab matching '{name[:60]}'."
        return None, "There are no open tabs to use."


def _a_tab_close(params: Dict[str, Any]) -> str:
    """'close this/the tab [called X]' -> close the resolved tab (one only)."""
    from .. import browser_tabs as _bt
    if (params or {}).get("all"):
        return ("I can only close one tab at a time — "
                "say which tab to close.")
    name = str((params or {}).get("target") or "").strip()
    cfg, page_or_reply = _tabs_resolve(name)
    if page_or_reply is None or isinstance(page_or_reply, str):
        return page_or_reply
    page = page_or_reply
    label = _tab_label(page)
    try:
        res = _bt.close(cfg, str(page.get("id", "")),
                        requested_by="voice:user", confirmed=True)
    except _bt.BrowserTabsError as exc:
        if exc.code in _TAB_UNREACHABLE_CODES:
            return _TABS_UNREACHABLE
        return f"I couldn't close {label}: {exc.detail[:160]}."
    if not isinstance(res, dict) or res.get("state") != "OK":
        detail = str((res or {}).get("detail") or "refused")[:160]
        return f"I couldn't close {label}: {detail}."
    return f"Closed {label}."


def _a_tab_duplicate(params: Dict[str, Any]) -> str:
    """'duplicate this/the tab [X]' -> open the same URL in a new tab."""
    from .. import browser_tabs as _bt
    name = str((params or {}).get("target") or "").strip()
    cfg, page_or_reply = _tabs_resolve(name)
    if page_or_reply is None or isinstance(page_or_reply, str):
        return page_or_reply
    page = page_or_reply
    label = _tab_label(page)
    url = str(page.get("url") or "")
    try:
        res = _bt.open(cfg, url, requested_by="voice:user", confirmed=True)
    except _bt.BrowserTabsError as exc:
        if exc.code in _TAB_UNREACHABLE_CODES:
            return _TABS_UNREACHABLE
        if exc.code == _bt.BAD_INPUT_CODE:
            return f"'{label}' has no web address I can duplicate."
        return f"I couldn't duplicate {label}: {exc.detail[:160]}."
    if not isinstance(res, dict) or res.get("state") != "OK":
        detail = str((res or {}).get("detail") or "refused")[:160]
        return f"I couldn't duplicate {label}: {detail}."
    return f"Duplicated {label} in a new tab."


def _a_tab_new(params: Dict[str, Any]) -> str:
    """'new tab' / 'open a new tab' -> a blank tab.

    browser_tabs.open() is http(s)-only by policy, and a blank tab carries no
    URL, so this calls the same loopback endpoint open() uses (PUT /json/new,
    no url query) with the same gates: e-stop, ledger remember, audit.
    """
    from .. import browser_tabs as _bt
    from .. import cdp as _cdp
    cfg = _tabs_cfg()
    if cfg is None:
        return "Browser control isn't configured on this machine."
    if _bt.estop_engaged(cfg):
        return "Emergency stop is latched; opening a tab is refused."
    try:
        created = _cdp.http_json("PUT", "/json/new")
    except _cdp.CdpError as exc:
        if exc.code == _cdp.UNAVAILABLE_CODE:
            return _TABS_UNREACHABLE
        return f"I couldn't open a new tab: {exc.detail[:160]}."
    if not isinstance(created, dict) or not created.get("id"):
        return "The browser didn't open a new tab."
    tid = str(created["id"])
    try:
        _bt._remember(cfg, tid)
    except Exception:
        pass
    log_event("browser_tabs.open", actor="voice:user", target=tid)
    return "Opened a new blank tab."


def _a_tab_switch(params: Dict[str, Any]) -> str:
    """'switch to/go to [the] X tab' -> bring the resolved tab to front."""
    from .. import browser_tabs as _bt
    name = str((params or {}).get("target") or "").strip()
    if not name:
        return "Which tab should I switch to?"
    cfg, page_or_reply = _tabs_resolve(name)
    if page_or_reply is None or isinstance(page_or_reply, str):
        return page_or_reply
    page = page_or_reply
    label = _tab_label(page)
    try:
        res = _bt.activate(cfg, str(page.get("id", "")),
                           requested_by="voice:user", confirmed=True)
    except _bt.BrowserTabsError as exc:
        if exc.code in _TAB_UNREACHABLE_CODES:
            return _TABS_UNREACHABLE
        return f"I couldn't switch to {label}: {exc.detail[:160]}."
    if not isinstance(res, dict) or res.get("state") != "OK":
        detail = str((res or {}).get("detail") or "refused")[:160]
        return f"I couldn't switch to {label}: {detail}."
    return f"Switched to {label}."


def _a_tab_list(params: Dict[str, Any]) -> str:
    """'what tabs are open' -> spoken 'N tabs: title1, title2…'."""
    from .. import browser_tabs as _bt
    cfg = _tabs_cfg()
    if cfg is None:
        return "Browser control isn't configured on this machine."
    try:
        out = _bt.tabs(cfg)
    except _bt.BrowserTabsError as exc:
        if exc.code in _TAB_UNREACHABLE_CODES:
            return _TABS_UNREACHABLE
        return f"I couldn't list the tabs: {exc.detail[:160]}."
    if not isinstance(out, dict) or out.get("state") != "OK":
        return _TABS_UNREACHABLE
    rows = [r for r in (out.get("tabs") or []) if isinstance(r, dict)]
    if not rows:
        return "No browser tabs are open."
    names = [_tab_label(r, 40) or "untitled tab" for r in rows[:7]]
    extra = len(rows) - len(names)
    tail = f", and {extra} more" if extra > 0 else ""
    plural = "s" if len(rows) != 1 else ""
    return f"{len(rows)} tab{plural} open: {', '.join(names)}{tail}."


def _a_send_message(params: Dict[str, Any]) -> str:
    """Send a staged voice-reply draft (confirm-gated upstream).

    Accepts ONLY the canonical ``reply|<channel>|<to>|<href>|<body>``
    draft built by the voice layer (never free text — the broker tier
    still demands a user-issued confirmation). Re-resolution at send
    time refuses silently-changed targets. Raises ValueError with a
    typed ``CODE: detail`` message on any refusal/failure.
    """
    from ..voice_edit import send as _send
    draft = _send.parse_draft(str((params or {}).get("draft") or ""))
    if draft is None:
        raise ValueError("SEND_MALFORMED: not a staged reply draft")
    out = _send.execute(draft, dry_run=False)
    if not out.get("ok"):
        raise ValueError(f"{out.get('code', 'SEND_FAILED')}: "
                         f"{out.get('detail', '')}"[:220])
    return f"Sent to {draft['to']} on {draft['channel']}."


def _a_compound(params: Dict[str, Any]) -> str:
    """Run each step (re-matched deterministically) in order; join replies."""
    raw = str((params or {}).get("steps") or "")
    halves = [h.strip(" .,") for h in raw.split(_COMPOUND_STEPS_SEP)]
    halves = [h for h in halves if h]
    if len(halves) < 2:
        return "I didn't catch the steps to run — say each one on its own."
    # Two-phase: validate every half BEFORE any side effect, so a stale
    # half can never leave step 1 done and step 2 refused.
    planned = [_match_single(h) for h in halves]
    if any(m is None or m.name == "compound"
           or not _compoundable((m.name,)) for m in planned):
        return "I couldn't run those together — say each one on its own."
    outs: List[str] = []
    for m in planned:
        act = ACTIONS.get(m.name)
        try:
            outs.append(act.fn(m.params))
        except Exception as exc:
            outs.append(f"({m.name} failed: {exc})")
    return " ".join(o for o in outs if o)


# --- Timer number-words (voice: "one minute" .. "thirty minutes") -----------
# Spoken durations arrive as words ("one", "two", ...) not digits. Map covers
# one..sixty (tens + twenty/forty/fifty/sixty compounds) plus the indefinite
# article ("a"/"an" minute == 1). Compounds ("twenty one" / "forty-two") are
# handled in _timer_minutes_from_word.
_TIMER_NUMBER_WORDS: Dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60,
    "a": 1, "an": 1,
}

#: Tens that take a ones compound ("twenty one" .. "sixty nine").
_TIMER_TENS: Dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
}

# Longest-first alternation so "twenty" doesn't shadow longer names; the
# tens-X compounds are matched by the duration regex in _match_single.
_TIMER_ONES_ALT = (r"one|two|three|four|five|six|seven|eight|nine")
_TIMER_WORD_ALT = (
    r"twenty[ -]?(?:one|two|three|four|five|six|seven|eight|nine)"
    r"|forty[ -]?(?:one|two|three|four|five|six|seven|eight|nine)"
    r"|fifty[ -]?(?:one|two|three|four|five|six|seven|eight|nine)"
    r"|sixty[ -]?(?:one|two|three|four|five|six|seven|eight|nine)"
    r"|nineteen|eighteen|seventeen|sixteen|fifteen|fourteen|thirteen|twelve"
    r"|eleven|sixty|fifty|forty|thirty|twenty|seven|three|eight|five|nine|four|one|two|six|ten"
    r"|a|an"
)

#: Hour units ("for 2 hours" -> 120 minutes, clamped by _a_timer as usual).
_TIMER_HOUR_ALT = r"hours?|hrs?"


def _timer_minutes_from_word(word: str) -> Optional[int]:
    """Convert a spoken number-word (or digit string) to minutes count."""
    w = (word or "").strip().lower().replace("-", " ")
    w = " ".join(w.split())
    if w.isdigit():
        return int(w)
    if w in _TIMER_NUMBER_WORDS:
        return _TIMER_NUMBER_WORDS[w]
    # tens-X compound ("twenty one" / "forty-two" with extra space)
    parts = w.split()
    if len(parts) == 2 and parts[0] in _TIMER_TENS \
            and parts[1] in _TIMER_NUMBER_WORDS:
        return _TIMER_TENS[parts[0]] + _TIMER_NUMBER_WORDS[parts[1]]
    return None


# ---------------------------------------------------------------------------
ACTIONS: Dict[str, LocalAction] = {
    "volume_up": LocalAction("volume_up", "raise speaker volume", _a_volume_up,
                             patterns=[r"\b(volume|sound|audio|speaker)[s]?\s+(up|louder|increase|raise|boost)\b",
                                       r"\b(raise|increase|boost)\s+(the\s+)?(volume|sound|audio|speaker)\b",
                                       r"\bmake (it|the volume) louder\b",
                                       r"\bturn (it|them|the (volume|sound|audio|speaker)) up\b",
                                       r"\bturn up (the )?(volume|sound|audio|speaker)\b"]),
    "volume_down": LocalAction("volume_down", "lower speaker volume", _a_volume_down,
                               patterns=[r"\b(volume|sound|audio|speaker)[s]?\s+(down|lower|decrease|reduce|quieter)\b",
                                         r"\b(lower|decrease|reduce)\s+(the\s+)?(volume|sound|audio|speaker)\b",
                                         r"\bmake (it|the volume) quieter\b",
                                         r"\bturn (it|them|the (volume|sound|audio|speaker)) down\b",
                                         r"\bturn down (the )?(volume|sound|audio|speaker)\b"]),
    "volume_mute": LocalAction("volume_mute", "toggle speaker mute", _a_volume_mute,
                               patterns=[r"\b(mute|unmute) (the )?(speaker|sound|volume)\b",
                                         r"\b(speaker|sound) (mute|off)\b",
                                         r"^\s*(un)?mute\s*$"]),
    "mic_mute": LocalAction("mic_mute", "toggle microphone mute", _a_mic_mute,
                            patterns=[r"\b(mute|unmute) (the |my )?(microphone|mic)\b",
                                      r"\b(microphone|mic) (is )?(mute|muted|off)\b"]),
    "open_app": LocalAction("open_app", "launch an application", _a_open_app,
                             patterns=[r"^\s*(please\s+)?(open|launch|start)\s+(the\s+|my\s+)?files?\s*\.?\s*$",
                                       r"\b(open|launch|start|run) (the )?(browser|editor|vscode|cursor|chromium|terminal|discord|telegram|files|file-manager|file manager|fl studio|spotify|chrome|firefox|code|console|calculator|settings|system monitor|system-monitor|clocks?)\b"]),
    # Project names are operator-local (config/local-actions.yaml
    # ``project_roots``); the patterns stay generic so no personal project
    # names are baked into the shipped product source.
    "open_project": LocalAction("open_project", "open a project in VS Code", _a_open_project,
                               patterns=[r"\b(open|launch|start) (the )?([a-z0-9][\w.-]*) (in|with|using) (vs ?code|the editor|editor)\b",
                                         r"\bopen ([a-z0-9][\w.-]*) (in|with|using) (vs ?code|editor)\b",
                                         r"\b(open|launch) (the )?([a-z0-9][\w.-]*) project\b"]),
    "create_document": LocalAction("create_document", "create and open a text document", _a_create_document,
                                   patterns=[r"\b(create|make)( me)? (a |the )?(new )?(text |txt )?(document|file|note)\b",
                                             r"\b(create|make)\b.{0,24}?\b(new )?(text |txt )?(document|file|note)\b"]),
    "tab_new": LocalAction("tab_new", "open a new blank browser tab", _a_tab_new,
                         patterns=[r"\bopen (a )?new (blank )?tab\b",
                                   r"\b(create|make)( me)?( a)? new (blank )?tab\b",
                                   r"^\s*(please\s+)?(open\s+)?(a\s+)?new (blank )?tab\s*\.?\s*$"]),
    "tab_list": LocalAction("tab_list", "list the open browser tabs", _a_tab_list,
                         patterns=[r"\bwhat tabs( are open)?\b",
                                   r"\bhow many tabs( are open)?\b",
                                   r"\bwhich tabs( are open)?\b",
                                   r"\blist (my |open |browser )?tabs\b",
                                   r"\bshow (me )?(my |open |browser |all )?tabs\b"]),
    "display_switch": LocalAction("display_switch", "move the active window to a display", _a_display_switch,
                         patterns=[r"\b(?:switch|move|send|put|shift|throw)\b.{0,30}?\b(?:tabs?|window|screen|it|this)\b.{0,20}?\b(?:to|on|onto)\b.{0,20}?\b(?:(?:first|second|third|1st|2nd|3rd|primary)\s+(?:display|monitor|screen)|(?:display|monitor|screen)\s*(?:number\s*)?\d)\b",
                                   r"\b(?:switch|move|send|put|shift|throw)\b.{0,30}?\b(?:to|on|onto)\b.{0,25}?\b(?:(?:first|second|third|1st|2nd|3rd|primary)\s+(?:display|monitor|screen)|(?:display|monitor|screen)\s*(?:number\s*)?\d)\b",
                                   r"\b(?:switch|change)\s+(?:the\s+)?(?:display|monitor|screen)\s+(?:to\s+)?(?:the\s+)?(?:first|second|third|1st|2nd|3rd)\b"]),
    "tab_switch": LocalAction("tab_switch", "switch to a browser tab", _a_tab_switch,
                         patterns=[r"\b(switch to|go to|show|bring|focus)\b.{0,40}?\btabs?\b",
                                   r"\bswitch (to|back|tabs?)\b(?!\S*\.(?:com|ro|org|net|io|dev|app|eu|uk|de|fr|md|gov|edu)\b)"]),
    "tab_duplicate": LocalAction("tab_duplicate", "duplicate a browser tab", _a_tab_duplicate,
                         patterns=[r"\b(duplicate|dupe)\b.{0,40}?\btabs?\b"]),
    "tab_close": LocalAction("tab_close", "close a browser tab", _a_tab_close,
                         patterns=[r"\bclose\b.{0,30}?\btabs?\b"]),
    "window_close": LocalAction("window_close", "close the active window", _a_window_close,
                         patterns=[r"\bclose (this |the |current )?window\b"]),
    "open_website": LocalAction("open_website", "open a website in the browser", _a_open_website,
                                patterns=[r"\b(?:open|launch|go to|visit|pop on|pull up|bring up)\b.{0,30}?\b(https?://\S+|(?<!://)www\.\S+|(?<!://)[a-z0-9-]+\.(?:com|ro|org|net|io|dev|app|eu|uk|de|fr|md|gov|edu)\b\S*)",
                                            r"\b(?:open|launch|go to|visit|pop on|pull up|bring up) (the )?(youtube music|youtube|gmail|mail|messenger|instagram|facebook|github|opencode(?:\.ai)?|qubit(?:\s+(?:web\s+)?dashboard)?|google|duckduckgo|wikipedia|dexonline)\b"]),
    "open_folder": LocalAction("open_folder", "open Downloads/Documents in Files", _a_open_folder,
                             patterns=[r"\b(open|launch|show|go to)\b.{0,30}?\bdownloads?\b",
                                       r"\b(open|launch|show|go to)\b.{0,30}?\bdocuments?\s+(folder|directory|dir)\b"]),
    # Slice 3 — VS Code edits by voice. Placed before open_document/
    # open_website so an explicit "in vscode / in code" file request wins
    # over the generic document/URL matchers; the patterns require a
    # path-like token or repo-name form, so document/app phrases keep
    # their existing owners (pinned by tests).
    "repo_edit": LocalAction("repo_edit", "stage/apply a voice change in a repo (confirm-gated)", _a_repo_edit,
                             patterns=[r"\bin\s+repo\s+([\w.-]+)\s*,?\s*change\b",
                                       r"\bchange\b.{0,80}?\bin\s+repo\s+([\w.-]+)",
                                       r"\bin\s+repo\s+([\w.-]+)\b"],
                             confirm=True),
    "vscode_open": LocalAction("vscode_open", "open a file in VS Code", _a_vscode_open,
                               patterns=[r"\bopen\s+\S*(?:[\w-]+\.\w[\w.]*|/[\w.~/-]*)\S*\s+in\s+(?:vs\s*code|code)\b",
                                         r"\bshow\s+\S+\s+in\s+(?:vs\s*code|code)\b",
                                         r"\bopen\s+(?:the\s+)?([\w.-]+)\s+in\s+(?:vs\s*code|code)\b"]),
    "code_mcp_probe": LocalAction("code_mcp_probe", "probe the VS Code native MCP integration (read-only)", _a_code_mcp_probe,
                                  patterns=[r"\bcode\s+--add-mcp\b",
                                            r"\bvs\s*code\s+mcp\b.{0,24}?\b(available|present|support|integration|installed)\b",
                                            r"\bmcp\b.{0,24}?\b(available|present)\b.{0,24}?\b(vs\s*code|code)\b",
                                            r"\bcheck\s+code\s+mcp\b"]),
    "open_document": LocalAction("open_document", "open a text document for writing", _a_open_document,
                                 patterns=[r"\bopen (a |the |this |that |my |new )+(text |txt )?(document|file|note)\b(?![\w-])",
                                           r"\bopen (text |txt )(document|note)\b(?![\w-])",
                                           r"\bopen (a |the |my )?new (text |txt )?(document|file|note)\b(?![\w-])",
                                           r"\bopen\b.{0,30}?\b(documents?|notes?|files?|txt)\b(?![\w-])"]),
    "screenshot": LocalAction("screenshot", "take a screenshot", _a_screenshot,
                               patterns=[r"\b(take|grab) (a |an? )?(screenshot|screen ?shot)\b",
                                         r"\bscreenshots?\b"]),
    "clipboard": LocalAction("clipboard", "read the clipboard", _a_clipboard,
                             patterns=[r"\b(read|what'?s on|what is on) (the )?clipboard\b",
                                       r"\b(show|display|open) (the )?clipboard\b"]),
    "play_youtube": LocalAction("play_youtube", "play a YouTube song/video", _a_play_youtube_params,
                                patterns=[r"\b(play|put on|queue|listen to|i want to (hear|watch|listen to))\b",
                                          r"\b(turn on|start a)\b.{0,24}?\b(music|songs?|videos?|tunes?|mix|playlist|youtube)\b",
                                          r"\b(?!pause\b|stop\b|resume\b|close\b|next\b).*\byoutube music\b",
                                          r"\b(play|queue|listen to)\b.*\b(music|song|youtube music|video)\b",
                                          r"\bplay\s+(?!the\s+(music|media|song|video)|it\b|pause|whatever|some|game|football|chess)\b"]),
    "yt_mode": LocalAction("yt_mode", "choose youtube play mode (tab vs background)", _a_yt_mode,
                           patterns=[r"\b(windowed|in a (browser )?tab|open it (in|on)|background|audio only|just audio|quietly|in the background|sound only|no tab|headless)\b"]),
    "youtube_pause": LocalAction("youtube_pause", "pause youtube playback", _a_youtube_pause,
                                 patterns=[r"\b(pause|stop playing|hold)\s+(the\s+)?(youtube|music|song|video)\b",
                                           r"\b(pause|mute) (it|the music)\b"]),
    "youtube_resume": LocalAction("youtube_resume", "resume youtube playback", _a_youtube_resume,
                                  patterns=[r"\b(resume|continue|unpause|unmute)\s+(the\s+)?(youtube|music|song|video)\b",
                                            r"\b(resume|continue) (it|the music)\b"]),
    "youtube_next": LocalAction("youtube_next", "play the next youtube result", _a_youtube_next,
                                # media noun REQUIRED: bare "next" hijacked phrases
                                # like "forecast for the next seven days"
                                patterns=[r"\b(next|skip)\s+(the\s+|this\s+|that\s+)?(song|video|track|one|result|youtube)\b",
                                          r"\b(youtube|music|video)(?:'s)?\s+next\b"],
                                confirm=False),
    "youtube_stop": LocalAction("youtube_stop", "stop youtube playback and close tabs", _a_youtube_stop,
                                 # Sweep lane (2026-10-05): the bare-pronoun
                                 # branches ("close it", "stop it", "shut it
                                 # down") fired with no music context — this
                                 # action CLOSES TABS, so a pronoun alone must
                                 # clarify, never stop. Music-named branches
                                 # ("the music", youtube/song/video) keep
                                 # their owners; "close this window/tab" is
                                 # claimed earlier by the window/tab pre-check.
                                 patterns=[r"\b(stop|close)\s+(the\s+)?(youtube|music|song|video)\b",
                                           r"\b(stop|close) the music\b",
                                           r"\bshut the music (down|off)\b",
                                            r"\bclose (the )?(youtube|music) tabs?\b",
                                            r"\b(quit|close|stop)\s+(the\s+)?youtube\b"]),
    "lock": LocalAction("lock", "lock the screen", _a_lock,
                        patterns=[r"\b(lock|lock the screen)\b"]),
    "brightness_up": LocalAction("brightness_up", "raise screen brightness", _a_brightness_up,
                        patterns=[r"\b(brightness|screen brightness|display brightness|backlight)\s+(up|higher|brighter|increase|raise|boost)\b",
                                  r"\b(increase|raise|boost)\s+(the\s+)?(brightness|backlight)\b",
                                  r"\bturn\s+(up\s+)?(the\s+)?(brightness|backlight|screen)(\s+up)?\b",
                                  r"\bbrighter\b"]),
    "brightness_down": LocalAction("brightness_down", "lower screen brightness", _a_brightness_down,
                        patterns=[r"\b(brightness|screen brightness|display brightness|backlight)\s+(down|lower|dimmer|decrease|reduce|dim)\b",
                                  r"\b(lower|decrease|reduce|dim)\s+(the\s+)?(brightness|backlight)\b",
                                  r"\bturn\s+(down\s+)?(the\s+)?(brightness|backlight|screen)(\s+down)?\b",
                                  r"\b(dimmer|dim the screen)\b"]),
    "system_stats": LocalAction("system_stats", "report CPU/RAM/disk stats", _a_system_stats,
                        patterns=[r"\b(system stats|system status|how'?s the system|how is the system|resource usage|cpu usage|memory usage|disk usage)\b",
                                  r"\b(how much (ram|memory|cpu|disk))\b",
                                  r"\b(battery (level|status|percent))\b"]),
    "timer": LocalAction("timer", "set a timer", _a_timer,
                        # The set-branch keeps the duration space inside the
                        # optional group: a bare "set a timer" (end of string)
                        # must match, not just "set a timer for ...".
                        patterns=[r"\b(set (a |an |the |my )?timer( for)?|start (a |an |the |my )?timer)\b",
                                  r"\b(timer for |remind me in |in )\s*(\d+|" + _TIMER_WORD_ALT + r")\s*(minute|minutes|min|mins|second|seconds|" + _TIMER_HOUR_ALT + r")\b",
                                  r"\bhalf an? hour\b"]),
    "quick_note": LocalAction("quick_note", "save a quick note", _a_quick_note,
                        patterns=[r"\b(note (that|this)|remember (that|this)|write down|jot down|save (a )?note)\b",
                                  r"\b(take a note|make a note)\b",
                                  r"\b(remind me (to |that )?)\b"]),
    "find_file": LocalAction("find_file", "find a file by name", _a_find_file,
                        patterns=[r"\b(find|locate|search for) (a |the )?file\b",
                                  r"\bwhere is (the )?file\b",
                                  r"\b(find|locate)\b.{0,24}?\bfile\b"]),
    "empty_trash": LocalAction("empty_trash", "empty the trash", _a_empty_trash,
                        patterns=[r"\b(empty (the )?trash|clear (the )?trash|empty (the )?bin)\b",
                                  r"\btake out the trash\b"]),
    "bluetooth_toggle": LocalAction("bluetooth_toggle", "toggle bluetooth", _a_bluetooth_toggle,
                                patterns=[r"\b(toggle bluetooth|bluetooth (on|off))\b",
                                          r"\b(turn bluetooth (on|off))\b",
                                          r"\b(turn (on|off) bluetooth)\b"]),
    "time_date": LocalAction("time_date", "say date and time", _a_time_date,
                        patterns=[r"\bwhat (day|date|time) is (it|today)\b",
                                  r"\bwhat('s| is) today('s)? (date|day)\b",
                                  r"\bwhat time is it\b",
                                  r"\b(current )?(date|time)\b"]),
    "compound": LocalAction("compound", "run two voice commands together, in order", _a_compound,
                         patterns=[]),
    "send_message": LocalAction("send_message",
                        "send a staged voice-reply draft (confirm-gated)",
                        _a_send_message,                        patterns=[r"^reply\|(gmail|messenger|instagram)\|"],
                        confirm=True),
}

APP_NAMES = {
    "browser": "browser", "chromium": "chromium", "chrome": "browser", "firefox": "browser",
    "editor": "editor", "vscode": "vscode", "vs code": "vscode", "code": "vscode",
    "cursor": "cursor",
    "terminal": "terminal", "console": "terminal", "discord": "discord",
    "telegram": "telegram", "files": "files", "file manager": "files",
    "file-manager": "files", "file": "files",
    "fl studio": "fl studio", "fl": "fl studio", "spotify": "spotify",
    "calculator": "calculator", "settings": "settings",
    "system monitor": "system monitor", "system-monitor": "system monitor",
    "clock": "clocks", "clocks": "clocks",
}


# --- Compound "A and B" commands -------------------------------------------
# Both halves must be known commands; they run in order and the replies join.
# Shape note (checked against voice_edit/tool_task.py + broker.py, read-only):
# ``propose_tool`` accepts any ACTIONS name with scalar ``params``, and the
# broker registers every ACTIONS entry with a scalar-only arg validator — so
# the steps travel as ONE plain string (re-split + re-matched at execute).
# A nested list/dict shape would be refused by the validator, and changing
# the broker is out of scope: no compound marker outside this module.
# Safety: a half that needs a broker grant (MUTATING/DESTRUCTIVE tier) is
# never folded in — the whole utterance falls through (None) to the agent
# path, which confirm-gates it. No partial execution, no grant bypass.
_COMPOUND_SPLIT_RE = r"\s+(?:and then|and|then)\s+"
_COMPOUND_STEPS_SEP = "\n"

#: Fallback when the broker tier table cannot be imported (same-process
#: import failure): mirrors broker._ACTION_TIERS' non-reversible names.
_NON_COMPOUNDABLE_FALLBACK = frozenset(
    {"send_message", "empty_trash", "window_close", "repo_edit"})

#: Slice 3: a voice repo edit is a write and must never fold into a "both
#: at once" compound run — even where the broker tier table is reachable
#: (a new action defaults to REVERSIBLE there). The whole utterance falls
#: through to the agent path, which confirm-gates it. No partial execution.
_NEVER_COMPOUND = frozenset({"repo_edit"})


def _compoundable(names) -> bool:
    """True only when every named action runs without a broker grant."""
    if any(n in _NEVER_COMPOUND for n in names):
        return False
    try:
        from ..broker import _ACTION_TIERS
        from ..capabilities import Tier
        allowed = (Tier.READ_ONLY, Tier.REVERSIBLE)
        return all(
            _ACTION_TIERS.get(n, Tier.REVERSIBLE) in allowed for n in names)
    except Exception:
        return all(n not in _NON_COMPOUNDABLE_FALLBACK for n in names)


def match(text: str) -> Optional[ActionMatch]:
    """Return the first matching local action, or None.

    A two-command utterance ("A and B" / "A and then B" / "A then B") where
    BOTH halves are known, grant-free commands matches as one ``compound``
    action that runs both in order. Anything else keeps the single-match
    contract below exactly (fail-closed: unknown or grant-gated halves fall
    back to the whole-string single match, or to None).
    """
    import re as _re
    raw = (text or "").strip()
    # Staged voice-reply drafts are machine text: never split them, even if
    # the dictated body contains " and " — they route as one draft below.
    if raw and _re.match(r"^reply\|(gmail|messenger|instagram)\|",
                         raw.lower()):
        return _match_single(text)
    if raw and _re.search(r"\s+(?:and then|and|then)\s+", raw, flags=_re.I):
        parts = [_p.strip(" .,") for _p in
                 _re.split(_COMPOUND_SPLIT_RE, raw, flags=_re.I)]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            singles = [_match_single(p) for p in parts]
            if all(s is not None for s in singles):
                if _compoundable(tuple(s.name for s in singles)):
                    return ActionMatch(
                        name="compound",
                        params={"steps": _COMPOUND_STEPS_SEP.join(parts)},
                        text=text)
                # Every half known but one needs a grant: fall through to
                # the agent path (which confirm-gates) — never a lossy
                # single, never a partial execution.
                return None
    return _match_single(text)


def _match_single(text: str) -> Optional[ActionMatch]:
    """Return the first matching local action, or None (no splitting)."""
    low = text.lower().strip()
    import re as _re
    # Staged voice-reply drafts are machine text (reply|<channel>|...):
    # route them before any fuzzy matcher can claim the words inside.
    if _re.match(r"^reply\|(gmail|messenger|instagram)\|", low):
        return ActionMatch(name="send_message", params={"draft": text.strip()},
                           text=text)
    # "open code" is the usual ASR rendering of "opencode". Resolve it to the
    # website before open_app's bare "code" alias can launch VS Code. Explicit
    # "vs code"/"visual studio code"/"code editor" phrasings stay VS Code.
    if _OPENCODE_RE.search(low) and not _VSCODE_CONTEXT_RE.search(low):
        return ActionMatch(name="open_website",
                           params={"url": "https://opencode.ai"}, text=text)
    # Youtube CONTROL verbs (pause/stop/resume/next/close + music/youtube) take
    # priority over "play youtube": "pause the youtube music" must not be routed
    # to play_youtube (which also matches the bare 'youtube music' substring).
    # Window/tab intents beat the YouTube-control pre-check below: "close this
    # tab" and "close this window" both contain close+this, which would
    # otherwise route to youtube_stop's bare "close this" branch. A tab word
    # WITH a music noun ("close the youtube tabs") stays a YouTube intent.
    _is_window_tab_intent = (
        _re.search(r"\bwindow\b", low)
        or (_re.search(r"\btabs?\b", low)
            and not _re.search(r"\b(youtube|music|song|video)\b", low))
    )
    # A play verb BEFORE any control verb means "play ...", not control:
    # "play next song videos" must play, while "pause then play" stays
    # control-first. Positions decide; ties keep control priority.
    _play_at = [_m.start() for _m in
                _re.finditer(r"\b(play|put on|queue|listen to)\b", low)]
    _ctl_at = [_m.start() for _m in
               _re.finditer(r"\b(pause|stop|resume|continue|close|shut|skip|next|unpause|unmute|quit|hold)\b", low)]
    _play_first = bool(_play_at) and (not _ctl_at or min(_play_at) < min(_ctl_at))
    if _re.search(r"\b(pause|stop|resume|continue|close|shut|skip|next|unpause|unmute|quit|hold)\b", low) \
            and _re.search(r"\b(youtube|music|song|video|it|this)\b", low) \
            and not _is_window_tab_intent \
            and not _play_first:
        for name in ("youtube_pause", "youtube_resume", "youtube_next", "youtube_stop"):
            act = ACTIONS.get(name)
            for pat in act.patterns:
                if _re.search(pat, low):
                    return ActionMatch(name=name, params={"query": text, "text": text}, text=text)
    for name, act in ACTIONS.items():
        for pat in act.patterns:
            import re
            m = re.search(pat, low)
            if m:
                params: Dict[str, Any] = {}
                if name == "open_app":
                    for word, canon in APP_NAMES.items():
                        if word in low:
                            params["app"] = canon
                            break
                elif name == "open_project":
                    for word in _project_roots():
                        if word in low:
                            params["project"] = word
                            break
                    if not params.get("project"):
                        # Only a configured project root is an open_project
                        # intent; otherwise let the utterance fall through to
                        # the normal task/coding classifier.
                        continue
                elif name == "quick_note":
                    import re as _re
                    m2 = _re.search(r"(?:note (?:that|this)|remember (?:that|this)|write down|jot down|save (?:a )?note|remind me (?:to |that )?)(.*)", low)
                    if not m2:
                        m2 = _re.search(r"(?:take a note|make a note)[, ]+(.*)", low)
                    if m2:
                        params["note"] = m2.group(1).strip(" :,-") or text
                    else:
                        params["note"] = text
                elif name == "timer":
                    import re as _re
                    # Half-hour idiom carries no number word.
                    if _re.search(r"\bhalf an? hour\b", low):
                        params["minutes"] = 30
                    else:
                        m2 = _re.search(r"(\d+|" + _TIMER_WORD_ALT + r")\s*(minute|minutes|min|mins|second|seconds|" + _TIMER_HOUR_ALT + r")\b", low)
                        if m2:
                            n = _timer_minutes_from_word(m2.group(1))
                            if n is None:
                                n = 5
                            unit = m2.group(2)
                            if _re.fullmatch(_TIMER_HOUR_ALT, unit):
                                n = n * 60
                            elif "second" in unit:
                                n = max(1, (n + 59)//60)
                            params["minutes"] = n
                        elif _re.search(r"\ban? hour\b", low):
                            params["minutes"] = 60
                        else:
                            params["minutes"] = 5
                elif name == "find_file":
                    import re as _re
                    m2 = _re.search(r"(?:find|locate|search for|where is) (?:a |the )?file\s*(?:named |called )?(.+)", low)
                    if m2:
                        params["query"] = m2.group(1).strip(" \"'.)")
                    else:
                        m3 = _re.search(r"(?:find|locate)\s+(.+?)\s+file\b", low)
                        if m3:
                            params["query"] = m3.group(1).strip(" \"'.)")
                        else:
                            params["query"] = ""
                elif name in ("create_document", "open_document"):
                    import re as _re
                    # Spoken content ("write hello world in it", "with the
                    # text ...", "saying ...", "containing ...") is extracted
                    # FIRST and blanked out, so the name regex below cannot
                    # swallow it (2026-10-04: "open a txt document and write
                    # hello world in it" opened an empty doc — the content
                    # was parsed nowhere and silently dropped).
                    params["content"] = _extract_doc_content(low)
                    if params["content"]:
                        # Greedy to end of string (mirrors the extraction
                        # patterns, which are all terminal clauses).
                        low = _re.sub(
                            r"\b(?:and\s+)?write\s+.+\s+in\s+it\b"
                            r"|\bwith\s+(?:the\s+)?(?:text|content)\s+.+"
                            r"|\bsaying\s+.+|\bcontaining\s+.+"
                            r"|\bthat\s+says\s+.+",
                            " ", low)
                    m2 = _re.search(
                        r"(?:called|named|titled|for)\s+(.+?)\s*$", low)
                    if m2:
                        params["name"] = _strip_beneficiary_tail(
                            m2.group(1))
                    else:
                        params["name"] = ""
                    if not params["name"] and name == "open_document":
                        # Loose named-open ("open the shopping list
                        # document"): the words between the opener and the
                        # doc noun are the name.
                        m3 = _re.search(
                            r"\bopen\b\s+(.*?)\s*(?:text\s+|txt\s+)?"
                            r"(?:documents?|files?|notes?|txt)\b", low)
                        cand = (m3.group(1) or "") if m3 else ""
                        cand = _re.sub(
                            r"\b(a|the|this|that|my|new|text|txt|in|vs|code)\b",
                            "", cand).strip(" ,.-")
                        params["name"] = _strip_beneficiary_tail(cand)
                    # VS Code opens only when explicitly named; otherwise the
                    # operating system's own .txt editor handles the file.
                    if _re.search(r"\bvs\s*code\b|\bin\s+code\b|\bwith\s+code\b",
                                  low):
                        params["editor"] = "vscode"
                    else:
                        params["editor"] = ""
                elif name == "display_switch":
                    params["display"] = _display_ordinal(low)
                elif name in ("tab_close", "tab_duplicate", "tab_switch"):
                    import re as _re
                    if name == "tab_close" and _re.search(r"\ball\b", low) \
                            and _re.search(r"\btabs?\b", low):
                        # "close all tabs": one-tab primitive only — the
                        # handler answers honestly without touching CDP.
                        params["all"] = True
                        params["target"] = ""
                    else:
                        stripped = _re.sub(
                            r"\b(close|duplicate|dupe|switch|go|show|bring|focus|back|to|on|the|this|that|these|those|current|my|browser|tabs?|called|named|titled|please|front|a|an)\b",
                            " ", low)
                        params["target"] = _re.sub(
                            r"\s+", " ", stripped).strip(" .,-")
                elif name == "open_folder":
                    params["folder"] = ("downloads" if "download" in low
                                        else "documents")
                elif name == "repo_edit":
                    m2 = _re.search(r"\bin\s+repo\s+([\w.-]+)\b", low)
                    params["repo"] = (m2.group(1) if m2 else "")
                    params["instruction"] = _extract_repo_instruction(
                        text, params["repo"])
                    old, new, fil = _extract_replace_parts(
                        params["instruction"])
                    params["old"] = old or ""
                    params["new"] = new or ""
                    params["file"] = fil or _extract_path_token(text)
                    params["confirmed"] = ""
                    params["path"] = ""
                elif name == "vscode_open":
                    m2 = _re.search(r"\bin\s+(?:vs\s*code|code)\b", low)
                    params["path"] = _extract_path_token(text)
                    params["repo"] = ""
                    if not params["path"]:
                        m3 = _re.search(
                            r"\bopen\s+(?:the\s+)?([\w.-]+)\s+in\s+"
                            r"(?:vs\s*code|code)\b", low)
                        if m3:
                            params["repo"] = m3.group(1)
                    m4 = _re.search(r"\bline\s+(\d+)\b", low)
                    params["line"] = m4.group(1) if m4 else ""
                elif name == "open_website":
                    params["url"] = _normalize_website_url(low)
                elif name == "play_youtube":
                    import re as _re
                    # Strip leading verb + casual fillers, and any leading "youtube music".
                    q = _re.sub(
                        r"^(please\s+|can you\s+|you know\s+|just\s+|um\s+|uh\s+)*"
                        r"(please\s+)?(can you\s+)?"
                        r"(play|put on|queue|start|turn on|i want to (hear|watch|listen to)|i want to|listen to)\b[\s:]?",
                        "", low, flags=_re.I).strip()
                    # Drop leading "youtube music" / "youtube".
                    q = _re.sub(r"^(youtube music|youtube)\s+", "", q, flags=_re.I).strip()
                    q1 = q
                    # Drop trailing "on youtube / music / video / a song / the song".
                    q = _re.sub(r"\s+(on\s+)?(youtube music|youtube|music|some music|a song|the song|this song|a video|a track|media)\s*$", "", q, flags=_re.I).strip(" .,")
                    # Drop leading "some lofi"/"the song NAME" filler.
                    q = _re.sub(r"^(some|the)\s*(song|music|video|track)?\s*", "", q).strip(" .,")
                    # Never strip down to nothing and fall back to the raw
                    # utterance (which resurrects the verb): prefer the
                    # post-verb query ("some music" is a fine search).
                    params["query"] = q or q1 or text
                    params["text"] = text
                elif name in ("youtube_pause", "youtube_resume", "youtube_next", "youtube_stop", "yt_mode"):
                    params["query"] = text
                    params["text"] = text
                return ActionMatch(name=name, params=params, text=text)
    return None


def execute(m: ActionMatch) -> str:
    act = ACTIONS.get(m.name)
    if act is None:
        return "Unknown action."
    try:
        msg = act.fn(m.params)
        log_event("local.action", action=m.name, result=msg[:200])
        return msg
    except Exception as exc:
        log_event("local.action.error", action=m.name, error=str(exc)[:160])
        return f"Action failed: {exc}"


# Detached-survivor entry: `python -m voicebridge.local.actions
# --fire-timer <id>` (systemd-run path) or `--fire-timer-in <s> <id>`
# (reparented-sleeper path). Writes only the timer ledger. Exit 0 whenever
# the record is known (an already-claimed race is an idempotent no-op);
# exit 2 only for an unknown id, 3 for bad usage.
if __name__ == "__main__":
    import time as _t

    _argv = sys.argv[1:]
    if _argv[:1] == ["--fire-timer-in"] and len(_argv) == 3:
        try:
            _t.sleep(max(0, int(_argv[1])))
        except Exception:
            pass
        _argv = ["--fire-timer", _argv[2]]
    if _argv[:1] == ["--fire-timer"] and len(_argv) == 2:
        _known = timer_status(_argv[1])
        if _known is None:
            print(f"unknown timer {_argv[1]}", file=sys.stderr)
            raise SystemExit(2)
        _res = fire_timer(_argv[1])
        # Exit 0 whenever the record is known: a lost claim race means the
        # other layer already fired (idempotent no-op, not a unit failure).
        if not _res.get("fired"):
            print("timer already claimed/fired", file=sys.stderr)
        raise SystemExit(0)
    print("usage: actions --fire-timer <id> | --fire-timer-in <s> <id>",
          file=sys.stderr)
    raise SystemExit(3)
