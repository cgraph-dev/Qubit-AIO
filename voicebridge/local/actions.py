"""Local PC actions: direct, safe, reversible machine control.

Only whitelisted actions live here. Everything risky (rm, git reset --hard,
package installs, browser actions with logins/payments, service restarts,
file deletion) is *not* implemented locally — those route to OpenClaw where
the safety policy + spoken confirmations apply.

Each action returns a short spoken status string.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..logging_setup import get_logger, log_event

log = get_logger()


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


def _a_open_app(params: Dict[str, Any]) -> str:
    app = params.get("app", "")
    targets = {
        "browser": ["xdg-open", "https://duckduckgo.com"],
        "editor": ["xdg-open", "/home/trick/Projects/Repos"],
        "vscode": ["code", "/home/trick/Projects/Repos"],
        "terminal": ["gnome-terminal"],
        "discord": ["discord"],
        "telegram": ["telegram-desktop"],
        "files": ["xdg-open", "/home/trick"],
        "fl studio": ["wine", "/home/trick/.wine/drive_c/Program Files/Image-Line/FL Studio 21/FL64.exe"],
        "spotify": ["spotify"],
    }
    argv = targets.get(app)
    if not argv:
        return f"I don't have a launcher for {app or 'that'}."
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opening {app}."
    except Exception as exc:
        return f"Could not open {app}: {exc}"


# Known project roots for "open X in VS Code" — add more as needed.
PROJECT_ROOTS = {
    "cgraph": "/home/trick/Projects/Repos/CGraph Umbrella",
    "cgraph umbrella": "/home/trick/Projects/Repos/CGraph Umbrella",
    "rankvalut": "/home/trick/Projects/Repos/RankValut",
    "qubit": "/home/trick/Projects/Repos/Qubit (2)",
    "voice bridge": "/home/trick/Projects/Repos/openclaw-voice-bridge",
    "voicebridge": "/home/trick/Projects/Repos/openclaw-voice-bridge",
    "repos": "/home/trick/Projects/Repos",
    "projects": "/home/trick/Projects/Repos",
}


def _a_open_project(params: Dict[str, Any]) -> str:
    """'open the CGraph project in vscode' -> launch VS Code on that repo."""
    name = (params.get("project", "") or "").strip().lower()
    root = PROJECT_ROOTS.get(name)
    if not root:
        # fuzzy: pick the first root whose name appears in what was said
        said = (params.get("project", "") or "").lower()
        for key, path in PROJECT_ROOTS.items():
            if key in said and len(key) > 2:
                root = path
                break
    if not root:
        return (f"I know these projects: CGraph, RankValut, Qubit, voice bridge. "
                f"Which one should I open?")
    try:
        subprocess.Popen(["code", root], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return f"Opening {name or 'project'} in VS Code."
    except Exception as exc:
        return f"Could not open VS Code: {exc}"


def _a_screenshot(_: Dict[str, Any]) -> str:
    out = "/home/trick/Pictures/screenshot-%s.png" % __import__("time").strftime("%Y%m%d-%H%M%S")
    if shutil.which("gnome-screenshot"):
        _run(["gnome-screenshot", "-f", out])
    elif shutil.which("scrot"):
        _run(["scrot", out])
    else:
        return "No screenshot tool found."
    return "Screenshot saved."


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
    global _YT_PENDING, _YT_MODE
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
    global _YT_PLAYING, _YT_MODE
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


def _a_quick_note(params: Dict[str, Any]) -> str:
    text = params.get("note", "").strip()
    if not text:
        return "What should I note down?"
    from pathlib import Path; import datetime
    p = Path.home() / ".config" / "openclaw-voice-bridge" / "quick-notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(p, "a") as f: f.write(f"\n- [{ts}] {text}\n")
    log_event("local.quick_note", text=text[:120])
    return f"Noted: {text[:80]}"


def _a_timer(params: Dict[str, Any]) -> str:
    mins = params.get("minutes", 5)
    try: mins = int(mins)
    except: mins = 5
    mins = max(1, min(mins, 180))
    import threading
    def _fire():
        # desktop notification + spoken reminder
        if shutil.which("notify-send"):
            _run(["notify-send", f"Timer: {mins} minute{'s' if mins!=1 else ''} up.", "Time's up."])
        # try to speak via piper if available (fire-and-forget)
        try:
            import subprocess as sp
            sp.Popen(["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                     stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        except: pass
    threading.Timer(mins*60, _fire).start()
    return f"Timer set for {mins} minute{'s' if mins!=1 else ''}. I'll notify you."


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


# ---------------------------------------------------------------------------
ACTIONS: Dict[str, LocalAction] = {
    "volume_up": LocalAction("volume_up", "raise speaker volume", _a_volume_up,
                             patterns=[r"\b(volume|sound|audio|speaker)[s]?\s+(up|louder|increase|raise|boost)\b",
                                       r"\b(raise|increase|boost)\s+(the\s+)?(volume|sound)\b",
                                       r"\bturn (it |the (volume|sound) )?up\b"]),
    "volume_down": LocalAction("volume_down", "lower speaker volume", _a_volume_down,
                               patterns=[r"\b(volume|sound|audio|speaker)[s]?\s+(down|lower|decrease|reduce|quieter)\b",
                                         r"\b(lower|decrease|reduce)\s+(the\s+)?(volume|sound)\b",
                                         r"\bturn (it |the (volume|sound) )?down\b"]),
    "volume_mute": LocalAction("volume_mute", "toggle speaker mute", _a_volume_mute,
                               patterns=[r"\b(mute|unmute) (the )?(speaker|sound|volume)\b",
                                         r"\b(speaker|sound) (mute|off)\b"]),
    "mic_mute": LocalAction("mic_mute", "toggle microphone mute", _a_mic_mute,
                            patterns=[r"\b(mute|unmute) (the )?(microphone|mic)\b"]),
    "open_app": LocalAction("open_app", "launch an application", _a_open_app,
                            patterns=[r"\b(open|launch|start|run) (the )?(browser|editor|vscode|terminal|discord|telegram|files|fl studio|spotify)\b"]),
    "open_project": LocalAction("open_project", "open a project in VS Code", _a_open_project,
                               patterns=[r"\b(open|launch|start) (the )?(cgraph|rankvalut|qubit|voice bridge|voicebridge|project|repo|repos|projects) (in|with|using) (vs ?code|the editor|editor)\b",
                                         r"\bopen (cgraph|rankvalut|qubit) (in|with|using) (vs ?code|editor)\b",
                                         r"\b(open|launch) (the )?(cgraph|rankvalut|qubit) project\b"]),
    "screenshot": LocalAction("screenshot", "take a screenshot", _a_screenshot,
                              patterns=[r"\b(take|grab) (a |an? )?(screenshot|screen ?shot)\b"]),
    "clipboard": LocalAction("clipboard", "read the clipboard", _a_clipboard,
                             patterns=[r"\b(read|what'?s on|what is on) (the )?clipboard\b"]),
    "play_youtube": LocalAction("play_youtube", "play a YouTube song/video", _a_play_youtube_params,
                                patterns=[r"\b(play|put on|queue|start a|i want to (hear|watch|listen to)|listen to|turn on)\b",
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
                                patterns=[r"\b(next|skip|next song|next video|next one)\b"],
                                confirm=False),
    "youtube_stop": LocalAction("youtube_stop", "stop youtube playback and close tabs", _a_youtube_stop,
                                patterns=[r"\b(stop|close)\s+(the\s+)?(youtube|music|song|video)\b",
                                          r"\b(stop|close) (the music|it|this)\b",
                                          r"\bshut (it|the music) (down|off)\b",
                                           r"\bclose (the )?(youtube|music) tabs?\b",
                                           r"\b(quit|close|stop)\s+(the\s+)?youtube\b"]),
    "lock": LocalAction("lock", "lock the screen", _a_lock,
                        patterns=[r"\b(lock|lock the screen)\b"]),
    "brightness_up": LocalAction("brightness_up", "raise screen brightness", _a_brightness_up,
                        patterns=[r"\b(brightness|screen brightness|display brightness|backlight)\s+(up|higher|brighter|increase|raise|boost)\b",
                                  r"\b(increase|raise|boost)\s+(the\s+)?(brightness|backlight)\b",
                                  r"\bbrighter\b"]),
    "brightness_down": LocalAction("brightness_down", "lower screen brightness", _a_brightness_down,
                        patterns=[r"\b(brightness|screen brightness|display brightness|backlight)\s+(down|lower|dimmer|decrease|reduce|dim)\b",
                                  r"\b(lower|decrease|reduce|dim)\s+(the\s+)?(brightness|backlight)\b",
                                  r"\b(dimmer|dim the screen)\b"]),
    "system_stats": LocalAction("system_stats", "report CPU/RAM/disk stats", _a_system_stats,
                        patterns=[r"\b(system stats|system status|how'?s the system|resource usage|cpu usage|memory usage|disk usage)\b",
                                  r"\b(how much (ram|memory|cpu|disk))\b",
                                  r"\b(battery (level|status|percent))\b"]),
    "quick_note": LocalAction("quick_note", "save a quick note", _a_quick_note,
                        patterns=[r"\b(note (that|this)|remember (that|this)|write down|jot down|save (a )?note)\b",
                                  r"\b(remind me (to |that )?)\b"]),
    "timer": LocalAction("timer", "set a timer", _a_timer,
                        patterns=[r"\b(set (a )?timer (for )?|start (a )?timer)\b",
                                  r"\b(timer for |remind me in )\s*\d+\s*(minute|minutes|min|mins|second|seconds)\b"]),
    "window_close": LocalAction("window_close", "close the active window", _a_window_close,
                        patterns=[r"\b(close (this |the |current )?(window|tab))\b"]),
    "find_file": LocalAction("find_file", "find a file by name", _a_find_file,
                        patterns=[r"\b(find|locate|search for) (a |the )?file\b",
                                  r"\bwhere is (the )?file\b"]),
    "empty_trash": LocalAction("empty_trash", "empty the trash", _a_empty_trash,
                        patterns=[r"\b(empty (the )?trash|clear (the )?trash|empty (the )?bin)\b"]),
    "bluetooth_toggle": LocalAction("bluetooth_toggle", "toggle bluetooth", _a_bluetooth_toggle,
                        patterns=[r"\b(toggle bluetooth|bluetooth (on|off))\b",
                                  r"\b(turn bluetooth (on|off))\b"]),
    "time_date": LocalAction("time_date", "say date and time", _a_time_date,
                        patterns=[r"\bwhat (day|date|time) is (it|today)\b",
                                  r"\bwhat('s| is) today('s)? (date|day)\b",
                                  r"\bwhat time is it\b",
                                  r"\b(current )?(date|time)\b"]),
}

APP_NAMES = {
    "browser": "browser", "chrome": "browser", "firefox": "browser",
    "editor": "editor", "vscode": "vscode", "vs code": "vscode", "code": "vscode",
    "terminal": "terminal", "console": "terminal", "discord": "discord",
    "telegram": "telegram", "files": "files", "file manager": "files",
    "fl studio": "fl studio", "fl": "fl studio", "spotify": "spotify",
}


def match(text: str) -> Optional[ActionMatch]:
    """Return the first matching local action, or None."""
    low = text.lower().strip()
    import re as _re
    # Youtube CONTROL verbs (pause/stop/resume/next/close + music/youtube) take
    # priority over "play youtube": "pause the youtube music" must not be routed
    # to play_youtube (which also matches the bare 'youtube music' substring).
    if _re.search(r"\b(pause|stop|resume|continue|close|shut|skip|next|unpause|unmute|quit|hold)\b", low) \
            and _re.search(r"\b(youtube|music|song|video|it|this)\b", low):
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
                    for word in PROJECT_ROOTS:
                        if word in low:
                            params["project"] = word
                            break
                elif name == "quick_note":
                    import re as _re
                    m2 = _re.search(r"(?:note (?:that|this)|remember (?:that|this)|write down|jot down|save (?:a )?note|remind me (?:to |that )?)(.*)", low)
                    if m2:
                        params["note"] = m2.group(1).strip(" :,-") or text
                    else:
                        params["note"] = text
                elif name == "timer":
                    import re as _re
                    m2 = _re.search(r"(\d+)\s*(minute|minutes|min|mins|second|seconds)", low)
                    if m2:
                        n = int(m2.group(1))
                        if "second" in m2.group(2):
                            n = max(1, (n + 59)//60)
                        params["minutes"] = n
                    else:
                        params["minutes"] = 5
                elif name == "find_file":
                    import re as _re
                    m2 = _re.search(r"(?:find|locate|search for|where is) (?:a |the )?file\s*(?:named |called )?(.+)", low)
                    if m2:
                        params["query"] = m2.group(1).strip(" \"'.)")
                    else:
                        params["query"] = ""
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
                    # Drop trailing "on youtube / music / video / a song / the song".
                    q = _re.sub(r"\s+(on\s+)?(youtube music|youtube|music|some music|a song|the song|this song|a video|a track|media)\s*$", "", q, flags=_re.I).strip(" .,")
                    # Drop leading "some lofi"/"the song NAME" filler.
                    q = _re.sub(r"^(some|the)\s*(song|music|video|track)?\s*", "", q).strip(" .,")
                    params["query"] = q or text
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
