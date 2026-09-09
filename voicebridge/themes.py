"""Packet 8T-F — Hermes pets/skin parity via optional Qubit themes.

Hermes ``skin {list,use,set}`` manages terminal skins and ``pets``
installs animated sprite companions from a public gallery. Qubit ships
one coherent visual language, so the Qubit-native conversion is optional
themes and cosmetics inside that system — never mascots, never gallery
downloads:

* ``theme list`` — built-in presets (read-only);
* ``theme show`` — active preset + user tweaks (read-only);
* ``theme set`` — switch preset (explicit ``--confirm``, e-stop gated,
  audited, reversible via ``reset`` or setting the previous preset);
* ``theme set-tweak``/``clear-tweak`` — per-token color overrides
  (validated ``#rgb``/``#rrggbb`` on known design tokens only);
* ``theme reset`` — restore the default preset with no tweaks;
* gallery pet downloads stay refused (``PETS_NO_DOWNLOADS``): fetching
  unpinned third-party sprite code violates the pinned-source policy.

Selection persists in ``state/ui-theme.json`` (machine-local preference,
atomic write). The dashboard applies it through the dynamically served
``/static/theme.css`` override (after ``app.css``); an unknown or corrupt
state file falls back to the default preset (fail closed to coherent).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict

SCHEMA = "qubit-ui-theme/1"
STORE_NAME = "ui-theme.json"
DEFAULT_THEME = "qubit-dark"

CONFIRM_CODE = "THEME_CONFIRM_REQUIRED"
ESTOP_CODE = "THEME_ESTOP"
UNKNOWN_CODE = "THEME_UNKNOWN"
BAD_COLOR_CODE = "THEME_BAD_COLOR"
PETS_CODE = "PETS_NO_DOWNLOADS"

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Tweakable design tokens (color tokens only; layout/typography stay fixed
# so every theme remains one coherent Qubit system).
TWEAKABLE = ("--bg", "--surface", "--surface-raised", "--surface-soft",
             "--text", "--muted", "--faint", "--line", "--line-strong",
             "--primary", "--primary-strong", "--success", "--warning",
             "--danger", "--focus")

# Preset overrides relative to frontend/app.css (the default dark system).
# Empty dict = app.css as shipped.
THEMES: Dict[str, Dict[str, Any]] = {
    "qubit-dark": {
        "label": "Qubit Dark (default)",
        "description": "Near-black surfaces with a restrained lime accent.",
        "overrides": {},
    },
    "qubit-light": {
        "label": "Qubit Light",
        "description": "Paper surfaces with the same lime accent.",
        "overrides": {
            "--bg": "#f5f5ef", "--surface": "#ffffff",
            "--surface-raised": "#ffffff", "--surface-soft": "#ecece4",
            "--text": "#171918", "--muted": "#4c4f48", "--faint": "#777970",
            "--line": "#d8d8ce", "--line-strong": "#a9aaa1",
            "--primary": "#5a7a00", "--primary-strong": "#425c00",
            "--success": "#3f7a2e", "--warning": "#8a5f00",
            "--danger": "#b3261e", "--focus": "#5a7a00",
        },
    },
    "qubit-contrast": {
        "label": "Qubit Contrast",
        "description": "High-contrast accessibility preset.",
        "overrides": {
            "--bg": "#000000", "--surface": "#000000",
            "--surface-raised": "#111111", "--surface-soft": "#000000",
            "--text": "#ffffff", "--muted": "#e8e8e8", "--faint": "#c9c9c9",
            "--line": "#8a8a8a", "--line-strong": "#ffffff",
            "--primary": "#e8ff8f", "--primary-strong": "#e8ff8f",
            "--success": "#a5df7b", "--warning": "#f0c56b",
            "--danger": "#ff8178", "--focus": "#e8ff8f",
        },
    },
}


class ThemeError(Exception):
    """Typed theme failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "theme": DEFAULT_THEME, "tweaks": {},
            "updated_at": 0.0, "updated_by": ""}


def load(cfg: Any) -> Dict[str, Any]:
    """Load the theme selection, failing closed to the default preset."""
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict) or raw.get("theme") not in THEMES:
        return _blank()
    tweaks = raw.get("tweaks")
    raw["tweaks"] = {k: v for k, v in tweaks.items()
                     if k in TWEAKABLE and isinstance(v, str)
                     and _HEX_RE.match(v)} if isinstance(tweaks, dict) else {}
    raw.setdefault("schema", SCHEMA)
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise ThemeError("THEME_IO", f"theme store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"theme.{op}", actor=actor, **detail)
    except Exception:
        pass


def list_themes() -> Dict[str, Any]:
    """Built-in presets (read-only)."""
    return {"state": "OK",
            "themes": [{"name": name, "label": meta["label"],
                        "description": meta["description"]}
                       for name, meta in THEMES.items()]}


def show(cfg: Any) -> Dict[str, Any]:
    """Active preset + tweaks (read-only)."""
    state = load(cfg)
    return {"state": "OK", "theme": state["theme"],
            "label": THEMES[state["theme"]]["label"],
            "tweaks": dict(state["tweaks"]),
            "css_href": "/static/theme.css"}


def set_theme(cfg: Any, name: str, *, requested_by: str = "cli:user",
              confirmed: bool = False) -> Dict[str, Any]:
    """Switch preset (audited; previous preset reported for reversal)."""
    if not confirmed:
        raise ThemeError(CONFIRM_CODE,
                         "theme set requires explicit --confirm")
    clean = (name or "").strip().lower()
    if clean not in THEMES:
        raise ThemeError(UNKNOWN_CODE,
                         f"unknown theme {name!r}; choices: "
                         f"{', '.join(sorted(THEMES))}")
    state = load(cfg)
    previous = state["theme"]
    state.update(theme=clean, updated_at=time.time(),
                 updated_by=requested_by)
    _save(cfg, state)
    _audit("set", requested_by, {"theme": clean, "previous": previous})
    return {"state": "OK", "theme": clean,
            "label": THEMES[clean]["label"], "previous": previous,
            "css_href": "/static/theme.css"}


def set_tweak(cfg: Any, token: str, color: str, *,
              requested_by: str = "cli:user",
              confirmed: bool = False) -> Dict[str, Any]:
    """Override one design token (validated hex on known tokens only)."""
    if not confirmed:
        raise ThemeError(CONFIRM_CODE,
                         "theme set-tweak requires explicit --confirm")
    clean_token = (token or "").strip()
    if not clean_token.startswith("--"):
        clean_token = "--" + clean_token
    if clean_token not in TWEAKABLE:
        raise ThemeError(UNKNOWN_CODE,
                         f"unknown token {token!r}; tweakable: "
                         f"{', '.join(TWEAKABLE)}")
    if not _HEX_RE.match((color or "").strip()):
        raise ThemeError(BAD_COLOR_CODE,
                         "color must be #rgb or #rrggbb hex")
    state = load(cfg)
    state["tweaks"][clean_token] = color.strip()
    state.update(updated_at=time.time(), updated_by=requested_by)
    _save(cfg, state)
    _audit("set-tweak", requested_by, {"token": clean_token})
    return {"state": "OK", "theme": state["theme"],
            "tweaks": dict(state["tweaks"])}


def clear_tweak(cfg: Any, token: str, *, requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Remove one token override (idempotent)."""
    if not confirmed:
        raise ThemeError(CONFIRM_CODE,
                         "theme clear-tweak requires explicit --confirm")
    clean_token = (token or "").strip()
    if not clean_token.startswith("--"):
        clean_token = "--" + clean_token
    state = load(cfg)
    state["tweaks"].pop(clean_token, None)
    state.update(updated_at=time.time(), updated_by=requested_by)
    _save(cfg, state)
    _audit("clear-tweak", requested_by, {"token": clean_token})
    return {"state": "OK", "theme": state["theme"],
            "tweaks": dict(state["tweaks"])}


def reset(cfg: Any, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Restore the default preset with no tweaks (the rollback)."""
    if not confirmed:
        raise ThemeError(CONFIRM_CODE,
                         "theme reset requires explicit --confirm")
    state = _blank()
    state.update(updated_at=time.time(), updated_by=requested_by)
    _save(cfg, state)
    _audit("reset", requested_by, {})
    return {"state": "OK", "theme": DEFAULT_THEME, "tweaks": {}}


def css(cfg: Any) -> str:
    """Render the ``/static/theme.css`` override (never raises)."""
    try:
        state = load(cfg)
        merged = dict(THEMES[state["theme"]]["overrides"])
        merged.update(state["tweaks"])
        if not merged:
            return ("/* Qubit theme: %s (default; app.css as shipped) */\n"
                    % state["theme"])
        lines = ["/* Qubit theme: %s (override layer after app.css) */"
                 % state["theme"], ":root {"]
        for token in sorted(merged):
            lines.append(f"  {token}: {merged[token]};")
        lines.append("}")
        return "\n".join(lines) + "\n"
    except Exception:
        return "/* Qubit theme: fallback to app.css as shipped */\n"


def refuse_pet_downloads() -> Dict[str, Any]:
    """Typed refusal for gallery pet downloads (pinned-source policy)."""
    return {"state": "REFUSED", "code": PETS_CODE,
            "explanation": "Qubit Agent cannot do this: installing gallery "
                           "pets would download unpinned third-party sprite "
                           "code; cosmetic customization lives in "
                           "`voicebridge theme` presets and token tweaks.",
            "owner": "qubit-policy",
            "rollback": "nothing installed; nothing to roll back"}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing the theme.",
            "owner": "qubit-policy",
            "rollback": "theme untouched; nothing to roll back"}
