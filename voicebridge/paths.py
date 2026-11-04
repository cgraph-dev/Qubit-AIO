"""Single source of truth for VoiceBridge on-disk layout.

Two layouts exist and both must resolve to the same answer in every caller
(Config, CLI, services, the root installer):

**New installs** live under the Qubit home contract::

    $QUBIT_HOME/voice/config    voice.yaml, .env, personal knowledge, templates
    $QUBIT_HOME/voice/data      data owned by the bridge
    $QUBIT_HOME/voice/state     state, journals, runtime pointers
    $QUBIT_HOME/voice/cache     cache
    $QUBIT_HOME/voice/models    model artifacts

``QUBIT_HOME`` defaults to ``~/.qubit`` (the same default as the vendored
runtime). New installs are *initialized* by the root installer, which creates
``$QUBIT_HOME/voice`` before anything else runs.

**Existing installs** keep their XDG paths::

    $XDG_CONFIG_HOME/qubit-voicebridge
    $XDG_DATA_HOME/qubit-voicebridge
    $XDG_STATE_HOME/qubit-voicebridge
    $XDG_CACHE_HOME/qubit-voicebridge
    $XDG_DATA_HOME/qubit-voicebridge/models

Those paths are *read-old*: this module never moves, copies, or deletes them.
``voice_layout()`` reports them explicitly so a caller can act on the existing
install rather than silently splitting it across two trees.

The module is deliberately dependency-free (no YAML, no package imports) so
``scripts/install.sh`` can ask it for the layout before the venv exists.

Resolution rules:

* ``layout.mode`` is ``"new"`` when ``$QUBIT_HOME/voice`` exists,
  ``"read-old"`` when it does not but any legacy XDG directory does, and
  ``"uninitialized"`` when neither exists.
* ``layout.install_mode`` is ``"new"`` for both ``"new"`` and
  ``"uninitialized"`` (a fresh install gets the new tree) and ``"read-old"``
  only when an existing install must be preserved.
* ``layout.effective`` is the runtime preference: the new tree for every
  install that is not read-old (``"new"`` and ``"uninitialized"``), and the
  XDG paths only in ``"read-old"``. An uninitialized machine has no existing
  data to preserve, so a runtime that starts before the installer still
  writes ``$QUBIT_HOME/voice`` — never a fresh XDG tree that would then be
  mistaken for a legacy install.

``QUBIT_HOME`` itself defaults to ``~/.qubit`` on Linux/macOS and to
``%LOCALAPPDATA%\\qubit`` on Windows, matching the vendored runtime and the
Windows installer.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

# ---------------------------------------------------------------------------
# Canonical product identity (Packet 8S). The internal import package stays
# ``voicebridge``; the two legacy names are compatibility-only.
# ---------------------------------------------------------------------------
PRODUCT_NAME = "qubit-voicebridge"
PRODUCT_TITLE = "Qubit VoiceBridge"
LEGACY_NAME = "openclaw-voice-bridge"
LEGACY_NAME_7D = "qubit-voice-agent"
LEGACY_NAMES = (LEGACY_NAME, LEGACY_NAME_7D)

#: Directory names under ``$QUBIT_HOME/voice``. Order is part of the report.
VOICE_KINDS = ("config", "data", "state", "cache", "models")
VOICE_SUBDIR = "voice"
DEFAULT_QUBIT_HOME_DIR = ".qubit"
DEFAULT_WINDOWS_QUBIT_HOME_DIR = "qubit"

# Snap-confinement redirects are LOUD, never silent: every decision is
# recorded here and surfaced via xdg_report() (product paths diagnostic,
# status output, repair/doctor flows).
_XDG_REDIRECTS: list[dict[str, Any]] = []
_XDG_VARS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
             "XDG_CACHE_HOME")
_XDG_DEFAULTS = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share",
                 "XDG_STATE_HOME": ".local/state", "XDG_CACHE_HOME": ".cache"}


def _environ(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _is_snap_confinement(p: Path) -> bool:
    """True when a path lives inside a snap sandbox (e.g. VS Code's
    ``~/snap/code/<rev>/...``). Snap-confined processes inherit sandbox
    XDG_*_HOME values that must not fragment the voice bridge's data tree."""
    return "snap" in p.parts


def _xdg_base(env: str, default: str,
              environ: Optional[Mapping[str, str]] = None) -> Path:
    """XDG-aware base directory: honored env var wins (whitespace ignored),
    otherwise ``~/<default>``. Never returns ``.``.

    A value inside snap confinement is NOT honored: the sandbox path would
    fragment installs per snap revision. It redirects to the real-home
    default LOUDLY (recorded in _XDG_REDIRECTS, reported by xdg_report())."""
    source = _environ(environ)
    raw = source.get(env, "")
    if raw is not None and str(raw).strip():
        candidate = Path(str(raw).strip()).expanduser()
        if _is_snap_confinement(candidate):
            effective = Path("~").expanduser() / default
            if not any(r["var"] == env and r["raw"] == str(raw).strip()
                       for r in _XDG_REDIRECTS):
                _XDG_REDIRECTS.append({
                    "var": env, "raw": str(raw).strip(),
                    "effective": str(effective),
                    "reason": "snap-confinement-redirect"})
            return effective
        return candidate
    return Path("~").expanduser() / default


def xdg_report() -> list[dict[str, Any]]:
    """Effective XDG resolution for every directory variable: raw value,
    effective base, and source (``env`` | ``default`` |
    ``snap-confinement-redirect``). Read-only; the redirect is never silent."""
    out: list[dict[str, Any]] = []
    for env in _XDG_VARS:
        raw = os.environ.get(env, "")
        raw_text = str(raw).strip() if raw is not None else ""
        effective = str(_xdg_base(env, _XDG_DEFAULTS[env]))
        redir = next((r for r in _XDG_REDIRECTS
                      if r["var"] == env and r["raw"] == raw_text), None)
        if redir is not None:
            source = "snap-confinement-redirect"
        elif raw_text:
            source = "env"
        else:
            source = "default"
        out.append({"var": env, "raw": raw_text, "effective": effective,
                    "source": source})
    return out


# ---------------------------------------------------------------------------
# Qubit-home VoiceBridge layout (new installs)
# ---------------------------------------------------------------------------
def qubit_home(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Runtime home used to place the new ``voice/`` tree.

    ``QUBIT_HOME`` (expanded) wins; otherwise the platform default — the same
    one the vendored runtime uses: ``~/.qubit`` on Linux/macOS and
    ``%LOCALAPPDATA%\\qubit`` on Windows. No vendored import is involved so
    this stays usable before the runtime is installed.

    The pre-rebrand external home (``~/.hermes`` / ``%LOCALAPPDATA%\\hermes``)
    is never accepted as the Qubit home: choosing it would create a new voice
    tree inside a read-old external install. It falls back to the canonical
    platform default, matching ``voicebridge.qubit_runtime.default_home()``.
    """
    source = _environ(environ)
    raw = str(source.get("QUBIT_HOME", "") or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not _is_legacy_external_home(candidate, source):
            return candidate
    if sys.platform == "win32":
        local_appdata = str(source.get("LOCALAPPDATA", "") or "").strip()
        base = Path(local_appdata) if local_appdata \
            else Path("~").expanduser() / "AppData" / "Local"
        return base / DEFAULT_WINDOWS_QUBIT_HOME_DIR
    return Path("~").expanduser() / DEFAULT_QUBIT_HOME_DIR


def _is_legacy_external_home(path: Path,
                              environ: Optional[Mapping[str, str]] = None) -> bool:
    """True for the pre-rebrand external home, which is read-old only."""
    candidates = [Path("~").expanduser() / ".hermes"]
    if sys.platform == "win32":
        local_appdata = str(_environ(environ).get("LOCALAPPDATA", "") or "").strip()
        if local_appdata:
            candidates.append(Path(local_appdata) / "hermes")
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    for candidate in candidates:
        try:
            if resolved == candidate.resolve(strict=False):
                return True
        except OSError:
            continue
    return False


def voice_home(environ: Optional[Mapping[str, str]] = None) -> Path:
    """``$QUBIT_HOME/voice`` — the root of the new install tree."""
    return qubit_home(environ) / VOICE_SUBDIR


def new_voice_dirs(environ: Optional[Mapping[str, str]] = None) -> Dict[str, Path]:
    root = voice_home(environ)
    return {kind: root / kind for kind in VOICE_KINDS}


def read_old_voice_dirs(
        environ: Optional[Mapping[str, str]] = None) -> Dict[str, Path]:
    """Explicit XDG paths used by every VoiceBridge install before this lane.

    These are reported, never written, by :func:`voice_layout`; a caller may
    read them (and may keep writing them while ``mode == "read-old"`` so an
    existing install does not split), but nothing here moves or deletes them.
    """
    source = _environ(environ)
    config = _xdg_base("XDG_CONFIG_HOME", ".config", source) / PRODUCT_NAME
    data = _xdg_base("XDG_DATA_HOME", ".local/share", source) / PRODUCT_NAME
    state = _xdg_base("XDG_STATE_HOME", ".local/state", source) / PRODUCT_NAME
    cache = _xdg_base("XDG_CACHE_HOME", ".cache", source) / PRODUCT_NAME
    return {
        "config": config,
        "data": data,
        "state": state,
        "cache": cache,
        "models": data / "models",
    }


def _exists(path: Path) -> bool:
    try:
        return path.exists() or path.is_symlink()
    except OSError:  # pragma: no cover - defensive (bad mount/permissions)
        return False


def existing_read_old_dirs(
        environ: Optional[Mapping[str, str]] = None) -> Dict[str, Path]:
    return {kind: path for kind, path in read_old_voice_dirs(environ).items()
            if _exists(path)}


@dataclass(frozen=True)
class VoiceLayout:
    """Resolved layout for one process/install.

    ``mode`` is ``new`` / ``read-old`` / ``uninitialized``.
    ``install_mode`` is what the root installer should create: ``new`` or
    ``read-old``. ``effective`` is what runtime callers should use.
    """

    qubit_home: Path
    voice_root: Path
    mode: str
    install_mode: str
    new: Dict[str, Path]
    read_old: Dict[str, Path]
    existing_read_old: Dict[str, Path]
    effective: Dict[str, Path]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "qubit_home": str(self.qubit_home),
            "voice_root": str(self.voice_root),
            "mode": self.mode,
            "install_mode": self.install_mode,
            "new": {k: str(v) for k, v in self.new.items()},
            "read_old": {k: str(v) for k, v in self.read_old.items()},
            "existing_read_old": {k: str(v)
                                  for k, v in self.existing_read_old.items()},
            "effective": {k: str(v) for k, v in self.effective.items()},
        }


def voice_layout(environ: Optional[Mapping[str, str]] = None) -> VoiceLayout:
    """Resolve the single VoiceBridge layout (read-only; touches no files)."""
    new = new_voice_dirs(environ)
    old = read_old_voice_dirs(environ)
    old_existing = existing_read_old_dirs(environ)
    root = voice_home(environ)

    new_ready = _exists(root) or any(_exists(p) for p in new.values())
    if new_ready:
        mode = "new"
    elif old_existing:
        mode = "read-old"
    else:
        mode = "uninitialized"

    if mode == "read-old":
        install_mode = "read-old"
        effective = dict(old)
    else:
        # ``new`` and ``uninitialized`` both install into the QUBIT_HOME tree.
        # An uninitialized machine has no existing data to preserve, so the
        # runtime writes the new tree instead of materializing an XDG tree that
        # the next resolution would misread as a legacy install.
        install_mode = "new"
        effective = dict(new)

    return VoiceLayout(
        qubit_home=qubit_home(environ),
        voice_root=root,
        mode=mode,
        install_mode=install_mode,
        new=new,
        read_old=old,
        existing_read_old=old_existing,
        effective=effective,
    )


def resolve_voice_dir(kind: str,
                      environ: Optional[Mapping[str, str]] = None) -> Path:
    """Effective directory for ``kind`` (new install / read-old existing)."""
    if kind not in VOICE_KINDS:
        raise ValueError(f"unknown voice layout kind: {kind!r}")
    return voice_layout(environ).effective[kind]


def voice_layout_report(
        environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """JSON-friendly layout report (used by the installer and diagnostics)."""
    return voice_layout(environ).as_dict()
