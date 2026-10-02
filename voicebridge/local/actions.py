"""Local PC actions: direct, safe, reversible machine control.

Only whitelisted actions live here. Everything risky (rm, git reset --hard,
package installs, browser actions with logins/payments, service restarts,
file deletion) is *not* implemented locally — those run as supervised
worker sessions where the safety policy + spoken confirmations apply.

Each action returns a short spoken status string.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
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
    target = _documents_dir() / f"{slug}.txt"
    try:
        if not target.exists():
            target.write_text("", encoding="utf-8")
    except Exception as exc:
        return f"Could not create {target.name}: {exc}."
    editor = str(params.get("editor") or "")
    opened = _open_in_editor(target, editor)
    if opened.startswith("could not open"):
        return f"Created {target.name} in Documents, but {opened}"
    if "isn't installed" in opened:
        return f"Created {target.name}. {opened}"
    return f"Created {target.name} and opened it in the editor."


#: Spoken site names -> canonical URLs (generic, operator-neutral).
_SITE_SHORTCUTS = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "messenger": "https://www.messenger.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "github": "https://github.com",
    "google": "https://www.google.com",
    "duckduckgo": "https://duckduckgo.com",
    "wikipedia": "https://www.wikipedia.org",
    "dexonline": "https://dexonline.ro",
}

_OPEN_VERBS = r"(?:open|launch|go to|visit)"


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
    m = _re.search(r"(https?://\S+|www\.\S+|[a-z0-9-]+\.(?:com|ro|org|net|io|dev|app|eu|uk|de|fr|md|gov|edu)\b\S*)", low)
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
    honestly, never guessed. No name opens a blank editor.
    """
    name = str(params.get("name") or "").strip().lower()
    editor = str(params.get("editor") or "")
    if name:
        docs = _documents_dir()
        cands = sorted(docs.glob("*.txt"))
        hit = next((p for p in cands if p.stem.lower() == name), None)
        if hit is None:
            hit = next((p for p in cands if name in p.stem.lower()), None)
        if hit is None:
            known = ", ".join(p.name for p in cands[:10]) or "no saved notes"
            return (f"I couldn't find {name!r} in Documents "
                    f"({known}). Say 'create a text document' to make one.")
        opened = _open_in_editor(hit, editor)
        if opened.startswith("could not open"):
            return f"Found {hit.name}, but {opened}"
        if "isn't installed" in opened:
            return f"Found {hit.name}. {opened}"
        return f"Opened {hit.name} in the editor."
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
                                patterns=[r"\b(?:open|launch|go to|visit)\b.{0,30}?\b(https?://\S+|(?<!://)www\.\S+|(?<!://)[a-z0-9-]+\.(?:com|ro|org|net|io|dev|app|eu|uk|de|fr|md|gov|edu)\b\S*)",
                                            r"\b(?:open|launch|go to|visit) (the )?(youtube|gmail|mail|messenger|instagram|facebook|github|google|duckduckgo|wikipedia|dexonline)\b"]),
    "open_folder": LocalAction("open_folder", "open Downloads/Documents in Files", _a_open_folder,
                             patterns=[r"\b(open|launch|show|go to)\b.{0,30}?\bdownloads?\b",
                                       r"\b(open|launch|show|go to)\b.{0,30}?\bdocuments?\s+(folder|directory|dir)\b"]),
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
                        patterns=[r"\b(set (a )?timer (for )?|start (a )?timer)\b",
                                  r"\b(timer for |remind me in )\s*\d+\s*(minute|minutes|min|mins|second|seconds)\b"]),
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
    {"send_message", "empty_trash", "window_close"})


def _compoundable(names) -> bool:
    """True only when every named action runs without a broker grant."""
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
                        m3 = _re.search(r"(?:find|locate)\s+(.+?)\s+file\b", low)
                        if m3:
                            params["query"] = m3.group(1).strip(" \"'.)")
                        else:
                            params["query"] = ""
                elif name in ("create_document", "open_document"):
                    import re as _re
                    m2 = _re.search(
                        r"(?:called|named|titled|for)\s+(.+?)\s*$", low)
                    if m2:
                        params["name"] = m2.group(1).strip(" \"'.)")
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
                        params["name"] = cand
                    # VS Code opens only when explicitly named; otherwise the
                    # operating system's own .txt editor handles the file.
                    if _re.search(r"\bvs\s*code\b|\bin\s+code\b|\bwith\s+code\b",
                                  low):
                        params["editor"] = "vscode"
                    else:
                        params["editor"] = ""
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
