"""Qubit Office themes: a fail-closed, data-only contract.

A theme is a declarative folder, never code. This module owns the contract
(``qubit-office-theme/1``), the loader, the validators, and the semantic-token
to CSS-variable generator that the server injects into the office document.

Design rules, all enforced here rather than by convention:

* **Data only.** Files may be ``.png/.jpg/.jpeg/.webp/.json/.txt``; no CSS,
  JS, HTML, symlinks, or remote URLs.
* **Fail closed.** Anything unknown, malformed, or unsafe is refused with a
  reason; an unknown theme falls back to ``classic``.
* **Typed tokens.** A token value is a colour or a length. Values that could
  carry a CSS exfiltration vector (``url(``, ``var(``, ``attr(`` ...) are
  refused, not escaped.
* **No operator paths.** ``source`` is a description; a filesystem path is a
  validation error.
* **No art in the repo.** :func:`validate_recipe` checks the shareable
  ``theme.recipe.json`` (tokens, agent recipes, credit/licence, no files);
  :func:`validate_theme` checks a locally built full pack.
* **No Pillow, no network, no writes.** The loader only reads.

The CSS allowlist below is the only set of Habblaud ``.ui-root`` variables a
theme may set. Qubit owns this mapping, so an upstream variable rename is a
one-line change here, never a pack migration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from voicebridge.paths import qubit_home
from voicebridge.resources import resource_path

FORMAT = "qubit-office-theme"
SCHEMA = 1
CLASSIC = "classic"

#: The only variables a theme may set on ``html body .ui-root``.
ALLOWED_THEME_VARS: Sequence[str] = (
    "--ui-bg",
    "--ui-bg-soft",
    "--ui-bg-solid",
    "--ui-bg-hover",
    "--ui-line",
    "--ui-line-strong",
    "--ui-text",
    "--ui-muted",
    "--ui-faint",
    "--ui-accent",
    "--ui-accent-soft",
    "--radius",
    "--radius-sm",
    "--st-working",
    "--st-waiting",
    "--st-done",
    "--st-idle",
    "--st-offline",
    "--st-shell",
    "--lv-ok",
    "--lv-warn",
    "--lv-crit",
)

#: Semantic token -> Qubit-owned CSS variable. Aliases emit the same variable.
TOKEN_MAP: Dict[str, str] = {
    "color-surface": "--ui-bg",
    "color-surface-soft": "--ui-bg-soft",
    "color-surface-raised": "--ui-bg-solid",
    "color-surface-solid": "--ui-bg-solid",
    "color-surface-hover": "--ui-bg-hover",
    "color-border": "--ui-line",
    "color-border-strong": "--ui-line-strong",
    "color-text-primary": "--ui-text",
    "color-text-muted": "--ui-muted",
    "color-text-faint": "--ui-faint",
    "color-accent": "--ui-accent",
    "color-accent-soft": "--ui-accent-soft",
    "radius-panel": "--radius",
    "radius-control": "--radius-sm",
    "color-status-working": "--st-working",
    "status-working": "--st-working",
    "color-status-waiting": "--st-waiting",
    "status-waiting": "--st-waiting",
    "color-status-done": "--st-done",
    "status-done": "--st-done",
    "color-status-idle": "--st-idle",
    "status-idle": "--st-idle",
    "color-status-offline": "--st-offline",
    "status-offline": "--st-offline",
    "color-status-shell": "--st-shell",
    "status-shell": "--st-shell",
    "color-level-ok": "--lv-ok",
    "level-ok": "--lv-ok",
    "color-level-warn": "--lv-warn",
    "level-warn": "--lv-warn",
    "color-level-crit": "--lv-crit",
    "level-crit": "--lv-crit",
}

#: Status/level tokens must reach 3:1 against the surface (non-text contrast).
STATUS_TOKENS: Dict[str, Sequence[str]] = {
    "--st-working": ("color-status-working", "status-working"),
    "--st-waiting": ("color-status-waiting", "status-waiting"),
    "--st-done": ("color-status-done", "status-done"),
    "--st-idle": ("color-status-idle", "status-idle"),
    "--st-offline": ("color-status-offline", "status-offline"),
    "--st-shell": ("color-status-shell", "status-shell"),
    "--lv-ok": ("color-level-ok", "level-ok"),
    "--lv-warn": ("color-level-warn", "level-warn"),
    "--lv-crit": ("color-level-crit", "level-crit"),
}

KNOWN_PARTS: Sequence[str] = ("skin", "eyes", "hair", "outfit", "accessory")

#: Habblaud's floor kinds (``client/src/art/api.ts``), in its declared order.
#: A pack may supply art for any subset; the others keep Habblaud's procedural
#: floor. The set is closed because the client switch only understands these.
FLOOR_KINDS: Sequence[str] = (
    "carpet",
    "wood",
    "tile_check",
    "tile_white",
    "concrete",
    "marble",
    "grass",
    "sidewalk",
    "street",
)
#: Floor art is a tile image, never JSON/JPEG. The loader serves the declared
#: file through the same confined static route as the atlases.
SURFACE_SUFFIXES: Set[str] = {".png", ".webp"}

#: Extended asset categories a full local pack may declare. The set is closed so
#: an unknown category is refused rather than silently ignored. A pack may omit
#: any category (characters-only, furniture-only, ...).
ASSET_CATEGORIES: Sequence[str] = (
    "characters",
    "furniture",
    "animated",
    "ui",
    "outside",
    "ui_extra",
    "icons",
    "effects",
    "tilesets",
    "frames",
)

#: Kind of entry inside an asset index. ``sprite`` is one standalone image,
#: ``sheet`` is a grid sheet, ``animated`` carries frame timing, and the UI
#: sheet variants name their intended use.
ASSET_KINDS: Sequence[str] = (
    "sprite",
    "sheet",
    "animated",
    "nine_slice_sheet",
    "button_icon_sheet",
    "gamepad_sheet",
)

#: Optional role metadata for the extra packs. It is descriptive only; the
#: renderer decides how to use an entry. Unknown roles are refused so a typo
#: does not silently produce an unusable pack.
ASSET_ROLES: Sequence[str] = (
    "ui_atlas",
    "button",
    "icon",
    "icon_sheet",
    "font_atlas",
    "font_glyph",
    "font_logo",
    "hud",
    "panel",
    "dialog",
    "bar",
    "tileset",
    "prop",
    "animation",
    "effect",
    "frame_sequence",
)

#: Provenance status for a pack whose licence/readme is missing.
LICENSE_STATUSES: Sequence[str] = ("declared", "unverified", "public-domain")

#: Room archetypes a full local pack may declare. The list is closed so a
#: typo is refused rather than silently producing an unrenderable room.
ROOM_ARCHETYPES: Sequence[str] = (
    "executive",
    "operations",
    "meeting",
    "focus",
    "reception",
    "kitchen",
    "restroom",
    "lounge",
)

#: Placement layers, back to front. A room document may only use these.
ROOM_LAYERS: Sequence[str] = (
    "floor",
    "wall",
    "door",
    "window",
    "rug",
    "furniture",
    "wall_art",
    "overlay",
)

#: Anchors describe where a placement's tile coordinate meets its sprite.
ROOM_ANCHORS: Sequence[str] = (
    "top-left",
    "top-center",
    "center",
    "bottom-center",
    "bottom-left",
    "bottom-right",
)

#: Body animation states the character sheets actually provide (measured
#: against Character Generator 2.0's AnimationSO data). ``type``/``talk`` are
#: office semantics mapped onto these real states; no dedicated typing or
#: talking body rows exist in the purchased sheets.
ANIMATION_STATES: Sequence[str] = (
    "idle",
    "walk",
    "sit",
    "sit2",
    "phone",
    "read",
)

#: Direction block order, measured from the walk row of the body sheet.
ANIMATION_DIRECTIONS: Sequence[str] = ("left", "down", "right", "up")

#: Office semantics -> real animation state. The mapping is data, not a
#: renderer assumption: ``type`` uses the sit row and ``talk`` the phone row.
OFFICE_ANIMATION_STATES: Mapping[str, str] = {
    "idle": "idle",
    "walk": "walk",
    "sit": "sit",
    "type": "sit",
    "talk": "phone",
}

ALLOWED_SUFFIXES: Set[str] = {".png", ".jpg", ".jpeg", ".webp", ".json", ".txt"}
WINDOWS_RESERVED: Set[str] = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

ASSET_BASE = "/plugins/qubit-office/themes"

#: A pack that omits ``limits`` still gets a bounded validation: the declared
#: archive bytes are compared against this cap before any file is opened, so an
#: unbounded manifest cannot make the loader hash an arbitrarily large tree.
DEFAULT_MAX_TOTAL_BYTES = 25 * 1024 * 1024

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_TOKEN_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
_LENGTH_RE = re.compile(r"^(0|[1-9][0-9]{0,3})(px|rem|em)$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SLICE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_ASSET_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")

#: The Habblaud ``FurnitureKind`` values a theme may replace with pack art.
#: Any other kind (or an absent one) keeps the procedural Habblaud sprite.
FURNITURE_KINDS: Sequence[str] = (
    "desk", "desk_back", "office_chair", "office_chair_front", "bookshelf",
    "filing_cabinet", "printer", "trash_bin", "plant_small", "plant_tall",
    "glass_partition", "binder_shelf", "meeting_table", "stool",
    "floor_lamp", "water_cooler", "whiteboard", "window", "clock", "poster",
    "painting", "tv", "light_switch", "elevator", "door_frame", "sign",
    "mirror", "shelf_wall", "counter", "counter_sink", "coffee_machine",
    "microwave", "fridge", "vending_machine", "cafe_table", "cafe_chair",
    "sofa", "armchair", "coffee_table", "pingpong_table", "beanbag", "arcade",
    "toilet_stall", "sink", "reception_desk", "bench",
)
_SLICE_WIDTH_RE = re.compile(r"^[1-9][0-9]{0,3}px$")
_SLICE_REPEAT = {"stretch", "round", "repeat"}

#: Slice names whose rule is derived from the BUTTON's own slice: a pack names
#: the state, the emitter attaches it to the button's state selector. The other
#: names (``dock``, ``card``, ``tab``, ``button``) target the element directly.
_SLICE_STATE_SELECTORS = {
    "button-hover": ('[data-office-slice="button"]:hover',),
    "button-pressed": (
        '[data-office-slice="button"]:active',
        '[data-office-slice="button"][aria-pressed="true"]',
    ),
}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: Asset ids are longer than persona slugs (they derive from source filenames).
_ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,160}$")

_DANGEROUS_TOKEN_VECTORS = (
    "url(", "image-set(", "src(", "var(", "attr(",
    "expression(", "javascript:", "@", "\\", ";", "}", "/*", "*/",
)

_CACHE: Dict[tuple, "Theme"] = {}
#: Parsed index ``cells`` per (index path, mtime_ns, size), so the 283-seat
#: panel does not re-read the same JSON once per seat.
_INDEX_CACHE: Dict[tuple, Dict[str, List[int]]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 64


class ThemeError(Exception):
    """A theme pack failed validation. ``problems`` carries every reason."""

    def __init__(self, problems: Iterable[str]):
        self.problems: List[str] = list(problems)
        super().__init__("; ".join(self.problems) or "invalid theme")


@dataclass(frozen=True)
class Theme:
    root: Path
    id: str
    label: str
    version: str
    credit: str
    license: str
    source: str
    template_version: int
    tokens: Mapping[str, str] = field(default_factory=dict)
    slices: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    portraits: Mapping[str, Any] = field(default_factory=dict)
    bodies: Mapping[str, Any] = field(default_factory=dict)
    surfaces: Mapping[str, Any] = field(default_factory=dict)
    assets: Mapping[str, Any] = field(default_factory=dict)
    characters: Mapping[str, Any] = field(default_factory=dict)
    furniture: Mapping[str, Any] = field(default_factory=dict)
    ui: Mapping[str, Any] = field(default_factory=dict)
    rooms: Mapping[str, Any] = field(default_factory=dict)
    animations: Mapping[str, Any] = field(default_factory=dict)
    files: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    agents: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Roots and config access
# --------------------------------------------------------------------------
def user_theme_root() -> Path:
    """The operator's local theme directory.

    This is the **only** root the static asset route serves from
    (:func:`theme_asset_path`). The bundled/source roots below remain valid for
    the loader/payload, but a pack that exists only there is never exposed over
    HTTP: serving it would make the built dashboard a theme delivery path and
    defeat the "local pack in the user home" contract.
    """
    return qubit_home() / "office" / "themes"


def theme_roots() -> List[Path]:
    """Where a theme may live, most specific first (loader/payload only)."""
    candidates = [
        user_theme_root(),
        resource_path("frontend/qubit-web/public/plugins/qubit-office") / "themes",
        resource_path("frontend/qubit-dist/plugins/qubit-office") / "themes",
    ]
    seen: Set[str] = set()
    roots: List[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            roots.append(candidate)
    return roots


def _cfg_get(cfg: Any, *path: str, default: Any = None) -> Any:
    """Read a config path from a Config-like object or a plain dict."""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        node: Any = cfg
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            return getter(*path, default=default)
        except Exception:  # noqa: BLE001 - a foreign config shape is not fatal
            return default
    return default


def _raw_theme_id(cfg: Any, requested: Optional[str] = None) -> str:
    """The raw id before validation. Env beats ``?theme=`` beats config."""
    env = os.environ.get("QUBIT_OFFICE_THEME")
    if env is not None and str(env).strip():
        return str(env).strip()
    if requested is not None and str(requested).strip():
        return str(requested).strip()
    raw = _cfg_get(cfg, "office", "theme", default=CLASSIC)
    if raw is None:
        return CLASSIC
    return str(raw).strip() or CLASSIC


def active_theme_id(cfg: Any, requested: Optional[str] = None) -> str:
    """The validated active theme id, or ``classic``.

    A well-formed id whose pack does not exist **or fails validation** is
    ``classic`` too: the caller must never build a host URL that the server
    would refuse to theme.
    """
    raw = _raw_theme_id(cfg, requested)
    if not raw or raw == CLASSIC or not _ID_RE.fullmatch(raw):
        return CLASSIC
    theme_dir = find_theme(raw)
    if theme_dir is None:
        return CLASSIC
    try:
        load_theme(theme_dir)
    except ThemeError:
        return CLASSIC
    return raw


def find_theme(theme_id: str) -> Optional[Path]:
    """Return the first pack directory for ``theme_id``, if any."""
    if not _ID_RE.fullmatch(theme_id or ""):
        return None
    for root in theme_roots():
        candidate = root / theme_id
        if (candidate / "theme.json").is_file():
            return candidate
    return None


# --------------------------------------------------------------------------
# Token validation
# --------------------------------------------------------------------------
def _hex_rgb(value: str) -> Optional[tuple]:
    if not isinstance(value, str) or not _COLOR_RE.fullmatch(value or ""):
        return None
    raw = value[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _relative_luminance(rgb: Sequence[int]) -> float:
    channels = []
    for channel in rgb:
        c = channel / 255.0
        channels.append(c / 12.92 if c <= 0.03928
                        else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 .. 21.0)."""
    a = _hex_rgb(first)
    b = _hex_rgb(second)
    if a is None or b is None:
        return 0.0
    la = _relative_luminance(a)
    lb = _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _effective_token_map(tokens: Mapping[str, Any]) -> Dict[str, str]:
    """The variable map ``token_css`` would emit (first token per variable)."""
    effective: Dict[str, str] = {}
    for token, variable in TOKEN_MAP.items():
        if token in tokens and variable not in effective:
            effective[variable] = tokens[token]
    return effective


#: Token variables whose foreground must clear 4.5:1 against BOTH office
#: surfaces. ``--ui-faint`` is the disabled text colour (the variable
#: ``color-text-faint`` emits): WCAG allows 3:1 for disabled controls, and we
#: hold it to that floor deliberately.
_TEXT_CONTRAST_VARS: Sequence[Tuple[str, float]] = (
    ("--ui-text", 4.5),
    ("--ui-muted", 4.5),
    ("--ui-faint", 3.0),
)

#: Non-text colours the office actually paints (accents, borders, the status
#: dots and the level pills). Each must clear 3:1 against both surfaces.
_NON_TEXT_CONTRAST_VARS: Sequence[str] = (
    "--ui-accent",
    "--ui-accent-soft",
    "--ui-line",
    "--ui-line-strong",
)

#: Muted text also renders on the hover/selected row surface (``--ui-bg-hover``),
#: not only the two base surfaces. The office plan holds it to 3:1 there.
_HOVER_CONTRAST_VARS: Sequence[Tuple[str, float]] = (
    ("--ui-muted", 3.0),
)


def _contrast_problems(tokens: Mapping[str, Any]) -> List[str]:
    """The full contrast matrix over the two office surfaces.

    The office renders on ``--ui-bg`` (the main surface) and ``--ui-bg-solid``
    (raised/plated surfaces). Text and muted text need 4.5:1; disabled text,
    accents, borders, status colours and level colours need 3:1. Only tokens
    the pack actually declares are measured (an absent token emits nothing).
    """
    problems: List[str] = []
    effective = _effective_token_map(tokens)
    surfaces = (
        ("--ui-bg", effective.get("--ui-bg")),
        ("--ui-bg-solid", effective.get("--ui-bg-solid")),
    )

    def check(variable: str, floor: float) -> None:
        value = effective.get(variable)
        if value is None or _hex_rgb(value) is None:
            return
        for surface_name, surface in surfaces:
            if surface is None or _hex_rgb(surface) is None:
                continue
            ratio = contrast_ratio(value, surface)
            if ratio + 1e-9 < floor:
                problems.append(
                    f"token contrast: {variable} vs {surface_name} is "
                    f"{ratio:.2f}:1; needs >= {floor}:1")

    for variable, floor in _TEXT_CONTRAST_VARS:
        check(variable, floor)
    for variable in _NON_TEXT_CONTRAST_VARS:
        check(variable, 3.0)
    for variable in STATUS_TOKENS:
        check(variable, 3.0)
    # Muted text also paints on the hover/selected row surface, which the two
    # base surfaces above do not cover.
    hover = effective.get("--ui-bg-hover")
    if hover is not None and _hex_rgb(hover) is not None:
        for variable, floor in _HOVER_CONTRAST_VARS:
            value = effective.get(variable)
            if value is None or _hex_rgb(value) is None:
                continue
            ratio = contrast_ratio(value, hover)
            if ratio + 1e-9 < floor:
                problems.append(
                    f"token contrast: {variable} vs --ui-bg-hover is "
                    f"{ratio:.2f}:1; needs >= {floor}:1")
    return problems


def validate_tokens(tokens: Any) -> List[str]:
    """Every reason a token map is unsafe or untyped."""
    if not isinstance(tokens, dict):
        return ["tokens: must be an object"]
    problems: List[str] = []
    for key, value in tokens.items():
        if not isinstance(key, str) or not _TOKEN_KEY_RE.fullmatch(key):
            problems.append(f"token {key!r}: invalid name")
            continue
        if not isinstance(value, str) or not value:
            problems.append(f"token {key}: must be a non-empty string")
            continue
        lowered = value.lower()
        if any(vector in lowered for vector in _DANGEROUS_TOKEN_VECTORS):
            problems.append(f"token {key}: unsafe value {value!r}")
            continue
        if not (_COLOR_RE.fullmatch(value) or _LENGTH_RE.fullmatch(value)):
            problems.append(
                f"token {key}: {value!r} is neither a colour nor a length")
    problems.extend(_contrast_problems(tokens))
    return problems


# --------------------------------------------------------------------------
# Slice validation
# --------------------------------------------------------------------------
def _validate_slices(slices: Any, files: Optional[Set[str]] = None) -> List[str]:
    if slices is None:
        return []
    if not isinstance(slices, dict):
        return ["slices: must be an object"]
    problems: List[str] = []
    for name, descriptor in slices.items():
        if not isinstance(name, str) or not _SLICE_NAME_RE.fullmatch(name):
            problems.append(f"slice {name!r}: invalid name")
            continue
        if not isinstance(descriptor, dict):
            problems.append(f"slice {name}: must be an object")
            continue
        src = descriptor.get("src")
        if not isinstance(src, str) or not src:
            problems.append(f"slice {name}: src is required")
        else:
            problems.extend(f"slice {name}: {p}" for p in _path_problems(src))
            if files is not None and src not in files:
                problems.append(
                    f"slice {name}: src {src!r} is not a declared file")
        cell = descriptor.get("slice")
        if (not isinstance(cell, (list, tuple)) or len(cell) != 4
                or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0
                       for v in cell)):
            problems.append(f"slice {name}: slice must be 4 positive integers")
        width = descriptor.get("width")
        if not isinstance(width, str) or not _SLICE_WIDTH_RE.fullmatch(width):
            problems.append(f"slice {name}: width must be a positive px length")
        repeat = descriptor.get("repeat")
        if repeat not in _SLICE_REPEAT:
            problems.append(
                f"slice {name}: repeat must be stretch, round, or repeat")
    return problems


def validate_slices(slices: Any, files: Optional[Set[str]] = None) -> List[str]:
    return _validate_slices(slices, files)


# --------------------------------------------------------------------------
# Path / file validation
# --------------------------------------------------------------------------
def _path_problems(path: str) -> List[str]:
    problems: List[str] = []
    if not isinstance(path, str) or not path:
        return ["path: must be a non-empty string"]
    if "\x00" in path or "\\" in path:
        problems.append(f"path {path!r}: backslash or NUL")
        return problems
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        problems.append(f"path {path!r}: must be relative")
        return problems
    if not _PATH_CHARS_RE.fullmatch(path):
        problems.append(f"path {path!r}: contains unsupported characters")
        return problems
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        problems.append(f"path {path!r}: empty, '.' or '..' segment")
        return problems
    suffix = os.path.splitext(segments[-1])[1].lower()
    if suffix not in ALLOWED_SUFFIXES:
        problems.append(f"path {path!r}: suffix {suffix or '(none)'} not allowed")
    basename = segments[-1]
    stem = basename[:-len(suffix)] if suffix else basename
    if stem.upper() in WINDOWS_RESERVED or basename.upper() in WINDOWS_RESERVED:
        problems.append(f"path {path!r}: Windows reserved basename")
    if basename != basename.rstrip(" ."):
        problems.append(f"path {path!r}: trailing dot or space")
    return problems


def _duplicate_problems(paths: Iterable[str]) -> List[str]:
    problems: List[str] = []
    nfc_seen: Dict[str, str] = {}
    case_seen: Dict[str, str] = {}
    for path in paths:
        if not isinstance(path, str):
            continue
        nfc = unicodedata.normalize("NFC", path)
        previous = nfc_seen.get(nfc)
        if previous is not None and previous != path:
            problems.append(
                f"path duplicate after NFC normalisation: {previous!r} and {path!r}")
        else:
            nfc_seen.setdefault(nfc, path)
        folded = nfc.casefold()
        previous = case_seen.get(folded)
        if previous is not None and previous != path:
            problems.append(
                f"path duplicate after case folding: {previous!r} and {path!r}")
        else:
            case_seen.setdefault(folded, path)
    return problems


def _inside_root(root: Path, relative: str) -> Optional[Path]:
    try:
        resolved_root = root.resolve()
        candidate = (root / relative).resolve()
        candidate.relative_to(resolved_root)
        return candidate
    except (OSError, ValueError):
        return None


def _symlink_component(root: Path, relative: str) -> Optional[str]:
    """The first symlinked component of ``relative``, checked before resolving."""
    current = root
    for part in relative.split("/"):
        current = current / part
        try:
            if current.is_symlink():
                return part
        except OSError:
            return part
    return None


def _file_limits(files: Any, limits: Any) -> Tuple[List[str], Optional[int]]:
    """Validate the declared limits and return the effective total-byte cap.

    A manifest that omits ``limits`` (or omits ``max_total_bytes``) still gets
    :data:`DEFAULT_MAX_TOTAL_BYTES`, so validation is bounded even when the pack
    author forgot to declare one.
    """
    problems: List[str] = []
    if limits is not None and not isinstance(limits, dict):
        problems.append("limits: must be an object")
        limits = {}
    declared = limits if isinstance(limits, dict) else {}
    for key in ("max_files", "max_total_bytes"):
        if key not in declared:
            continue
        value = declared[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"limits.{key}: must be a positive integer")
    max_files = declared.get("max_files")
    if isinstance(max_files, int) and not isinstance(max_files, bool):
        if len(files) > max_files:
            problems.append(
                f"files: {len(files)} entries exceed max_files={max_files}")
    max_total = declared.get("max_total_bytes")
    if not isinstance(max_total, int) or isinstance(max_total, bool) or max_total <= 0:
        max_total = DEFAULT_MAX_TOTAL_BYTES
    return problems, max_total


def _validate_files(files: Any, root: Optional[Path],
                    limits: Any) -> List[str]:
    if not isinstance(files, dict) or not files:
        return ["files: must be a non-empty object"]
    problems: List[str] = _duplicate_problems(files.keys())
    limit_problems, max_total = _file_limits(files, limits)
    problems.extend(limit_problems)
    total_bytes = 0
    # First pass is structural only: the declared total is checked against the
    # cap BEFORE any file is opened, so an over-budget pack is refused without
    # reading (and hashing) its tree.
    entries: List[Tuple[str, Any, List[str], Any, Any]] = []
    for relative, entry in files.items():
        path_problems = _path_problems(relative)
        problems.extend(path_problems)
        if not isinstance(entry, dict):
            problems.append(f"file {relative}: entry must be an object")
            continue
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            problems.append(f"file {relative}: sha256 must be 64 hex characters")
        size = entry.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"file {relative}: bytes must be a non-negative integer")
            size = None
        else:
            total_bytes += size
        entries.append((relative, entry, path_problems, size, sha))

    over_budget = total_bytes > max_total
    if over_budget:
        problems.append(
            f"files: {total_bytes} bytes exceed max_total_bytes={max_total}")
    if root is None or over_budget:
        return problems

    for relative, _entry, path_problems, size, sha in entries:
        if path_problems:
            continue
        link = _symlink_component(root, relative)
        if link is not None:
            problems.append(
                f"file {relative}: symlink component {link!r} is not allowed")
            continue
        candidate = _inside_root(root, relative)
        if candidate is None:
            problems.append(f"file {relative}: resolves outside the theme root")
            continue
        if not candidate.is_file():
            problems.append(f"file {relative}: missing")
            continue
        try:
            actual_size = candidate.stat().st_size
            if isinstance(size, int) and not isinstance(size, bool) and actual_size != size:
                problems.append(
                    f"file {relative}: bytes {actual_size} != declared {size}")
            # Do not read a file that is already over the total cap even if its
            # declared bytes lied.
            if actual_size > max_total:
                problems.append(
                    f"file {relative}: {actual_size} bytes exceed "
                    f"max_total_bytes={max_total}")
                continue
            actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if isinstance(sha, str) and _SHA256_RE.fullmatch(sha) and actual_sha.lower() != sha.lower():
                problems.append(f"file {relative}: sha256 mismatch")
        except OSError as exc:
            problems.append(f"file {relative}: unreadable ({exc})")
    return problems


def _validate_parts(parts: Any) -> List[str]:
    if parts is None:
        return []
    if not isinstance(parts, dict):
        return ["parts: must be an object"]
    problems: List[str] = []
    for key, value in parts.items():
        if not isinstance(key, str) or not _TOKEN_KEY_RE.fullmatch(key):
            problems.append(f"parts {key!r}: invalid name")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append(f"parts {key}: must be a non-negative integer")
    return problems


def _validate_agents(agents: Any, parts: Any) -> List[str]:
    if agents is None:
        return []
    if not isinstance(agents, dict):
        return ["agents: must be an object"]
    problems: List[str] = []
    counts = parts if isinstance(parts, dict) else {}
    for slug, recipe in agents.items():
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            problems.append(f"agent {slug!r}: invalid slug")
            continue
        if not isinstance(recipe, dict):
            problems.append(f"agent {slug}: recipe must be an object")
            continue
        for part, index in recipe.items():
            if part not in KNOWN_PARTS:
                problems.append(f"agent {slug}: unknown part {part!r}")
                continue
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                problems.append(
                    f"agent {slug}: {part} index must be a non-negative integer")
                continue
            limit = counts.get(part)
            if isinstance(limit, int) and not isinstance(limit, bool) and index >= limit:
                problems.append(
                    f"agent {slug}: {part} index {index} out of bounds "
                    f"(parts has {limit})")
    return problems


def _validate_asset_section(name: str, section: Any,
                            files: Set[str]) -> List[str]:
    if section is None:
        return [f"{name}: required"]
    if not isinstance(section, dict):
        return [f"{name}: must be an object"]
    problems: List[str] = []
    for key in ("atlas", "index"):
        value = section.get(key)
        if not isinstance(value, str) or not value:
            problems.append(f"{name}.{key}: required")
            continue
        problems.extend(f"{name}.{key}: {p}" for p in _path_problems(value))
        if files and value not in files:
            problems.append(f"{name}.{key}: {value!r} is not a declared file")
    size = section.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size not in (16, 32, 48):
        problems.append(f"{name}.size: must be 16, 32, or 48")
    for key in ("cols", "rows"):
        value = section.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"{name}.{key}: must be a positive integer")
    if name == "bodies":
        frame = section.get("frame")
        if not isinstance(frame, dict):
            problems.append("bodies.frame: must be an object")
        else:
            for key in ("w", "h"):
                value = frame.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    problems.append(f"bodies.frame.{key}: must be a positive integer")
    return problems


# --------------------------------------------------------------------------
# Characters / furniture / UI (optional, additive to the floor surfaces)
# --------------------------------------------------------------------------
def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value >= 0)


def _validate_characters_full(section: Any,
                              files: Optional[Set[str]]) -> List[str]:
    """Validate the full-pack ``characters`` block (one atlas of body frames)."""
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["characters: must be an object"]
    problems: List[str] = []
    atlas = section.get("atlas")
    if not isinstance(atlas, str) or not atlas:
        problems.append("characters.atlas: path is required")
    else:
        problems.extend(f"characters.atlas: {p}" for p in _path_problems(atlas))
        if files is not None and atlas not in files:
            problems.append(
                f"characters.atlas: {atlas!r} is not a declared file")
    cell = section.get("cell")
    if not isinstance(cell, dict):
        problems.append("characters.cell: must be an object")
    else:
        for key in ("w", "h"):
            value = cell.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                problems.append(
                    f"characters.cell.{key}: must be a positive integer")
    for key in ("cols", "rows"):
        value = section.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"characters.{key}: must be a positive integer")
    count = section.get("count")
    if count is not None:
        cols = section.get("cols")
        rows = section.get("rows")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            problems.append("characters.count: must be a positive integer")
        elif (isinstance(cols, int) and isinstance(rows, int)
              and not isinstance(cols, bool) and not isinstance(rows, bool)
              and count > cols * rows):
            problems.append("characters.count: exceeds cols*rows")
    anchor = section.get("anchor")
    if anchor is not None:
        if not isinstance(anchor, dict):
            problems.append("characters.anchor: must be an object")
        else:
            for key in ("x", "y"):
                if not _number(anchor.get(key)):
                    problems.append(
                        f"characters.anchor.{key}: must be a non-negative number")
    scale = section.get("scale")
    if scale is not None and (not isinstance(scale, (int, float))
                              or isinstance(scale, bool) or scale <= 0
                              or scale > 4):
        problems.append("characters.scale: must be a number in (0, 4]")
    return problems


def _validate_characters_recipe(section: Any) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["characters: must be an object"]
    problems: List[str] = []
    sheet = section.get("sheet")
    if not isinstance(sheet, str) or not sheet:
        problems.append("characters.sheet: is required")
    else:
        problems.extend(f"characters.sheet: {p}" for p in _path_problems(sheet))
    for key in ("x", "y", "w", "h"):
        value = section.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append(
                f"characters.{key}: must be a non-negative integer")
    for key in ("cols", "rows", "count"):
        value = section.get(key)
        if value is not None and (not isinstance(value, int)
                                  or isinstance(value, bool) or value <= 0):
            problems.append(f"characters.{key}: must be a positive integer")
    return problems


def _validate_furniture_full(section: Any,
                             files: Optional[Set[str]]) -> List[str]:
    """Validate the full-pack ``furniture`` map (kind -> declared image)."""
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["furniture: must be an object"]
    problems: List[str] = []
    for kind, descriptor in section.items():
        if not isinstance(kind, str) or kind not in FURNITURE_KINDS:
            problems.append(
                f"furniture: unknown furniture kind {kind!r}")
            continue
        if not isinstance(descriptor, dict):
            problems.append(f"furniture.{kind}: must be an object")
            continue
        src = descriptor.get("src")
        if not isinstance(src, str) or not src:
            problems.append(f"furniture.{kind}.src: path is required")
        else:
            problems.extend(
                f"furniture.{kind}.src: {p}" for p in _path_problems(src))
            if files is not None and src not in files:
                problems.append(
                    f"furniture.{kind}.src: {src!r} is not a declared file")
        for key in ("ax", "ay"):
            value = descriptor.get(key)
            if value is not None and not _number(value):
                problems.append(
                    f"furniture.{kind}.{key}: must be a non-negative number")
        scale = descriptor.get("scale")
        if scale is not None and (not isinstance(scale, (int, float))
                                  or isinstance(scale, bool) or scale <= 0
                                  or scale > 4):
            problems.append(
                f"furniture.{kind}.scale: must be a number in (0, 4]")
    return problems


def _validate_furniture_recipe(section: Any) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["furniture: must be an object"]
    problems: List[str] = []
    for kind, descriptor in section.items():
        if not isinstance(kind, str) or kind not in FURNITURE_KINDS:
            problems.append(f"furniture: unknown furniture kind {kind!r}")
            continue
        if not isinstance(descriptor, dict):
            problems.append(f"furniture.{kind}: must be an object")
            continue
        sheet = descriptor.get("sheet")
        if not isinstance(sheet, str) or not sheet:
            problems.append(f"furniture.{kind}.sheet: is required")
        else:
            problems.extend(
                f"furniture.{kind}.sheet: {p}" for p in _path_problems(sheet))
        for key in ("x", "y", "w", "h"):
            value = descriptor.get(key)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                problems.append(
                    f"furniture.{kind}.{key}: must be a non-negative integer")
    return problems


def _validate_ui_full(section: Any, files: Optional[Set[str]]) -> List[str]:
    """Validate the full-pack ``ui`` block (Modern UI 9-slice backgrounds)."""
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["ui: must be an object"]
    problems: List[str] = []
    slices = section.get("slices")
    if slices is not None:
        problems.extend(
            f"ui.{p}" if not p.startswith("slices") else p
            for p in _validate_slices(slices, files))
    return problems


def _validate_ui_recipe(section: Any) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["ui: must be an object"]
    slices = section.get("slices")
    if slices is None:
        return []
    if not isinstance(slices, dict):
        return ["ui.slices: must be an object"]
    problems: List[str] = []
    for name, descriptor in slices.items():
        if not isinstance(name, str) or not _SLICE_NAME_RE.fullmatch(name):
            problems.append(f"ui.slices {name!r}: invalid name")
            continue
        if not isinstance(descriptor, dict):
            problems.append(f"ui.slices.{name}: must be an object")
            continue
        sheet = descriptor.get("sheet")
        if not isinstance(sheet, str) or not sheet:
            problems.append(f"ui.slices.{name}.sheet: is required")
        else:
            problems.extend(
                f"ui.slices.{name}.sheet: {p}" for p in _path_problems(sheet))
        for key in ("x", "y", "w", "h"):
            value = descriptor.get(key)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                problems.append(
                    f"ui.slices.{name}.{key}: must be a non-negative integer")
        cell = descriptor.get("slice")
        if (not isinstance(cell, (list, tuple)) or len(cell) != 4
                or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0
                       for v in cell)):
            problems.append(
                f"ui.slices.{name}.slice: must be 4 positive integers")
        width = descriptor.get("width")
        if not isinstance(width, str) or not _SLICE_WIDTH_RE.fullmatch(width):
            problems.append(
                f"ui.slices.{name}.width: must be a positive px length")
        repeat = descriptor.get("repeat")
        if repeat not in _SLICE_REPEAT:
            problems.append(
                f"ui.slices.{name}.repeat: must be stretch, round, or repeat")
    return problems


# --------------------------------------------------------------------------
# Floor surfaces
# --------------------------------------------------------------------------
def _validate_surfaces_full(surfaces: Any, files: Optional[Set[str]]) -> List[str]:
    """Validate the full-pack ``surfaces`` block (declared art files)."""
    if surfaces is None:
        return []
    if not isinstance(surfaces, dict):
        return ["surfaces: must be an object"]
    problems: List[str] = []
    cell = surfaces.get("cell")
    if not isinstance(cell, int) or isinstance(cell, bool) or cell <= 0:
        problems.append("surfaces.cell: must be a positive integer")
    floor = surfaces.get("floor")
    if not isinstance(floor, dict) or not floor:
        problems.append("surfaces.floor: must be a non-empty object")
        return problems
    for kind, relative in floor.items():
        if not isinstance(kind, str) or kind not in FLOOR_KINDS:
            problems.append(
                f"surfaces.floor: unknown floor kind {kind!r} "
                f"(allowed: {', '.join(FLOOR_KINDS)})")
            continue
        if not isinstance(relative, str) or not relative:
            problems.append(f"surfaces.floor.{kind}: path is required")
            continue
        problems.extend(
            f"surfaces.floor.{kind}: {p}" for p in _path_problems(relative))
        suffix = os.path.splitext(relative)[1].lower()
        if suffix not in SURFACE_SUFFIXES:
            problems.append(
                f"surfaces.floor.{kind}: suffix {suffix or '(none)'} not allowed")
        if files is not None and relative not in files:
            problems.append(
                f"surfaces.floor.{kind}: {relative!r} is not a declared file")
    return problems


def _validate_surfaces_recipe(surfaces: Any) -> List[str]:
    """Validate the recipe ``surfaces`` block (coordinates only, no art)."""
    if surfaces is None:
        return []
    if not isinstance(surfaces, dict):
        return ["surfaces: must be an object"]
    problems: List[str] = []
    cell = surfaces.get("cell")
    if not isinstance(cell, int) or isinstance(cell, bool) or cell <= 0:
        problems.append("surfaces.cell: must be a positive integer")
    floor = surfaces.get("floor")
    if not isinstance(floor, dict) or not floor:
        problems.append("surfaces.floor: must be a non-empty object")
        return problems
    for kind, descriptor in floor.items():
        if not isinstance(kind, str) or kind not in FLOOR_KINDS:
            problems.append(
                f"surfaces.floor: unknown floor kind {kind!r} "
                f"(allowed: {', '.join(FLOOR_KINDS)})")
            continue
        if not isinstance(descriptor, dict):
            problems.append(f"surfaces.floor.{kind}: must be an object")
            continue
        sheet = descriptor.get("sheet")
        if not isinstance(sheet, str) or not sheet:
            problems.append(f"surfaces.floor.{kind}.sheet: is required")
        else:
            problems.extend(
                f"surfaces.floor.{kind}.sheet: {p}" for p in _path_problems(sheet))
        for key in ("col", "row"):
            value = descriptor.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(
                    f"surfaces.floor.{kind}.{key}: must be a non-negative integer")
    return problems


# --------------------------------------------------------------------------
# Extended asset catalog
# --------------------------------------------------------------------------
def _source_label_problems(value: Any, label: str) -> List[str]:
    """Validate a *description* of a source sheet (not a served path).

    Source labels come from the purchased archive's member names, which can
    contain spaces (``Character Pieces/...``). They are metadata only, so the
    checks are about safety (no absolute/operator path, traversal, URL or
    control characters), not the strict asset-path allowlist used for files we
    actually write and serve.
    """
    if not isinstance(value, str) or not value.strip():
        return [f"{label}: must be a non-empty string"]
    if len(value) > 400:
        return [f"{label}: must be at most 400 characters"]
    if any(ord(ch) < 32 for ch in value):
        return [f"{label}: must not contain control characters"]
    if "\\" in value:
        return [f"{label}: must not contain a backslash"]
    if "://" in value:
        return [f"{label}: must not be a URL"]
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return [f"{label}: must be relative"]
    if _looks_like_path(value):
        return [f"{label}: must be a source description, not a filesystem path"]
    return []


def _bounded_text_problems(value: Any, label: str) -> List[str]:
    """A bounded free-text field (credit/licence prose may carry a URL)."""
    if not isinstance(value, str) or not value.strip():
        return [f"{label}: must be a non-empty string"]
    if len(value) > 300:
        return [f"{label}: must be at most 300 characters"]
    if any(ord(ch) < 32 for ch in value):
        return [f"{label}: must not contain control characters"]
    return []


def _validate_frame(frame: Any, label: str) -> List[str]:
    if not isinstance(frame, dict):
        return [f"{label}: must be an object"]
    problems: List[str] = []
    for key in ("w", "h"):
        value = frame.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"{label}.{key}: must be a positive integer")
    count = frame.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        problems.append(f"{label}.count: must be a positive integer")
    if "fps" in frame and frame.get("fps") is not None:
        fps = frame.get("fps")
        if (not isinstance(fps, (int, float)) or isinstance(fps, bool)
                or fps <= 0):
            problems.append(f"{label}.fps: must be a positive number or null")
    return problems


def _validate_grid(grid: Any, label: str) -> List[str]:
    if grid is None:
        return []
    if not isinstance(grid, dict):
        return [f"{label}: must be an object"]
    problems: List[str] = []
    for key in ("cell", "cols", "rows"):
        value = grid.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"{label}.{key}: must be a positive integer")
    return problems


def _validate_kind_counts(value: Any, label: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"{label}: must be an object"]
    problems: List[str] = []
    for kind, count in value.items():
        if kind not in ASSET_KINDS:
            problems.append(f"{label}: unknown kind {kind!r}")
            continue
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            problems.append(f"{label}.{kind}: must be a non-negative integer")
    return problems


def _validate_asset_provenance(assets: Any) -> List[str]:
    """Validate the optional per-pack licence/credit metadata.

    The block is metadata only (no files, no art). It is what makes a pack
    honest about an unlicensed source instead of silently claiming a licence.
    """
    if not isinstance(assets, dict):
        return []
    packs = assets.get("packs")
    if packs is None:
        return []
    if not isinstance(packs, dict):
        return ["assets.packs: must be an object"]
    problems: List[str] = []
    for pack_id, entry in packs.items():
        label = f"assets.packs.{pack_id}"
        if not isinstance(pack_id, str) or not _ASSET_KIND_RE.fullmatch(pack_id):
            problems.append(f"{label}: invalid pack id")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be an object")
            continue
        for key in ("name", "credit", "license", "source"):
            if key in entry:
                problems.extend(_bounded_text_problems(
                    entry.get(key), f"{label}.{key}"))
        status = entry.get("status")
        if status is not None and status not in LICENSE_STATUSES:
            problems.append(
                f"{label}.status: must be one of {', '.join(LICENSE_STATUSES)}")
        extra = sorted(set(entry) - {"name", "credit", "license", "source",
                                     "status"})
        if extra:
            problems.append(
                f"{label}: may not carry {', '.join(extra)} (metadata only)")
    return problems


def _validate_assets_recipe(assets: Any) -> List[str]:
    """Validate the shareable recipe ``assets`` block: metadata only.

    A recipe may state how many sprites of each kind a local build carries. It
    may **not** carry index paths, sheet names, file paths, or hashes — that
    would be a file map of the paid art in the repo.
    """
    if assets is None:
        return []
    if not isinstance(assets, dict):
        return ["assets: must be an object"]
    problems: List[str] = []
    schema = assets.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 1:
        problems.append("assets.schema: must be 1")
    categories = assets.get("categories")
    if not isinstance(categories, dict) or not categories:
        problems.append("assets.categories: must be a non-empty object")
        return problems
    problems.extend(_validate_asset_provenance(assets))
    for category, config in categories.items():
        if category not in ASSET_CATEGORIES:
            problems.append(f"assets.categories: unknown category {category!r}")
            continue
        if not isinstance(config, dict):
            problems.append(f"assets.categories.{category}: must be an object")
            continue
        extra = sorted(set(config) - {"count", "kinds"})
        if extra:
            problems.append(
                f"assets.categories.{category}: recipe metadata may not carry "
                f"{', '.join(extra)} (art is built locally)")
        count = config.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            problems.append(
                f"assets.categories.{category}.count: must be a non-negative integer")
        problems.extend(_validate_kind_counts(
            config.get("kinds"), f"assets.categories.{category}.kinds"))
    return problems


def _validate_assets_full(assets: Any, files: Set[str]) -> List[str]:
    """Validate the full-pack ``assets`` block (index files are declared)."""
    if assets is None:
        return []
    if not isinstance(assets, dict):
        return ["assets: must be an object"]
    problems: List[str] = []
    schema = assets.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 1:
        problems.append("assets.schema: must be 1")
    categories = assets.get("categories")
    if not isinstance(categories, dict) or not categories:
        problems.append("assets.categories: must be a non-empty object")
        return problems
    problems.extend(_validate_asset_provenance(assets))
    for category, config in categories.items():
        if category not in ASSET_CATEGORIES:
            problems.append(f"assets.categories: unknown category {category!r}")
            continue
        if not isinstance(config, dict):
            problems.append(f"assets.categories.{category}: must be an object")
            continue
        index = config.get("index")
        if not isinstance(index, str) or not index:
            problems.append(f"assets.categories.{category}.index: required")
        else:
            problems.extend(
                f"assets.categories.{category}.index: {p}"
                for p in _path_problems(index))
            if os.path.splitext(index)[1].lower() != ".json":
                problems.append(
                    f"assets.categories.{category}.index: must be a .json file")
            if files and index not in files:
                problems.append(
                    f"assets.categories.{category}.index: {index!r} is not a "
                    "declared file")
        count = config.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            problems.append(
                f"assets.categories.{category}.count: must be a non-negative "
                "integer")
        problems.extend(_validate_kind_counts(
            config.get("kinds"), f"assets.categories.{category}.kinds"))
    return problems


def _validate_assets_index(root: Path, files: Mapping[str, Any],
                           category: str, config: Mapping[str, Any]) -> List[str]:
    """Validate one category index against the declared file map."""
    relative = config.get("index")
    if not isinstance(relative, str) or not relative:
        return []
    path = _inside_root(root, relative)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"assets.categories.{category}.index: invalid JSON ({exc})"]
    if not isinstance(data, dict):
        return [f"assets.categories.{category}.index: must be an object"]
    problems: List[str] = []
    schema = data.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 1:
        problems.append(f"assets.categories.{category}.index: schema must be 1")
    declared_category = data.get("category")
    if declared_category != category:
        problems.append(
            f"assets.categories.{category}.index: category "
            f"{declared_category!r} != {category!r}")
    sprites = data.get("sprites")
    if not isinstance(sprites, list):
        return problems + [
            f"assets.categories.{category}.index: sprites must be a list"]
    count = config.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count != len(sprites):
        problems.append(
            f"assets.categories.{category}: count {count} != {len(sprites)} "
            "index entries")
    seen_ids: Set[str] = set()
    for position, entry in enumerate(sprites):
        label = f"assets.categories.{category}.index.sprites[{position}]"
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be an object")
            continue
        sprite_id = entry.get("id")
        if not isinstance(sprite_id, str) or not _ASSET_ID_RE.fullmatch(sprite_id):
            problems.append(f"{label}.id: invalid asset id")
        elif sprite_id in seen_ids:
            problems.append(f"{label}.id: duplicate asset id {sprite_id!r}")
        else:
            seen_ids.add(sprite_id)
        kind = entry.get("kind")
        if kind not in ASSET_KINDS:
            problems.append(f"{label}.kind: unknown kind {kind!r}")
        problems.extend(_source_label_problems(
            entry.get("sheet"), f"{label}.sheet"))
        sprite_file = entry.get("file")
        if not isinstance(sprite_file, str) or not sprite_file:
            problems.append(f"{label}.file: required")
        else:
            problems.extend(
                f"{label}.file: {p}" for p in _path_problems(sprite_file))
            if sprite_file not in files:
                problems.append(
                    f"{label}.file: {sprite_file!r} is not a declared file")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            problems.append(f"{label}.sha256: must be 64 hex characters")
        source_sha = entry.get("source_sha256")
        if not isinstance(source_sha, str) or not _SHA256_RE.fullmatch(source_sha):
            problems.append(
                f"{label}.source_sha256: must be 64 hex characters")
        elif isinstance(sprite_file, str) and isinstance(files.get(sprite_file), dict):
            declared_sha = files[sprite_file].get("sha256")
            if isinstance(declared_sha, str) and declared_sha.lower() != source_sha.lower():
                problems.append(
                    f"{label}.source_sha256: does not match the declared file")
        size = entry.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"{label}.bytes: must be a non-negative integer")
        problems.extend(_validate_frame(entry.get("frame"), f"{label}.frame"))
        problems.extend(_validate_grid(entry.get("grid"), f"{label}.grid"))
        if "nine_slice_candidate" in entry and not isinstance(
                entry.get("nine_slice_candidate"), bool):
            problems.append(f"{label}.nine_slice_candidate: must be a boolean")
        frame_source = entry.get("frame_source")
        if frame_source is not None and frame_source not in (
                "gif", "sheet", "inferred", "static"):
            problems.append(
                f"{label}.frame_source: must be gif, sheet, inferred, or static")
        view = entry.get("view")
        if view is not None and view not in ("body", "portrait", "sheet"):
            problems.append(f"{label}.view: must be body, portrait, or sheet")
        for key in ("license", "credit", "pack"):
            if key in entry and entry.get(key) is not None:
                problems.extend(_bounded_text_problems(
                    entry.get(key), f"{label}.{key}"))
        role = entry.get("role")
        if role is not None and role not in ASSET_ROLES:
            problems.append(f"{label}.role: unknown role {role!r}")
    return problems


# --------------------------------------------------------------------------
# Room blueprints and character animation metadata
# --------------------------------------------------------------------------
def _validate_int(value: Any, label: str, *, minimum: int = 0) -> List[str]:
    if (not isinstance(value, int) or isinstance(value, bool)
            or value < minimum):
        return [f"{label}: must be an integer >= {minimum}"]
    return []


def _validate_tile_ref(value: Any, label: str) -> List[str]:
    if not isinstance(value, dict):
        return [f"{label}: must be an object"]
    problems: List[str] = []
    for key in ("col", "row"):
        problems.extend(_validate_int(value.get(key), f"{label}.{key}"))
    return problems


def _validate_room_rect(value: Any, label: str, size: Mapping[str, int]
                        ) -> List[str]:
    problems: List[str] = []
    for key in ("x", "y", "w", "h"):
        problems.extend(_validate_int(value.get(key), f"{label}.{key}"))
    for key, bound in (("x", "w"), ("y", "h")):
        value_key = value.get(key)
        span_key = value.get(bound)
        if (isinstance(value_key, int) and not isinstance(value_key, bool)
                and isinstance(span_key, int) and not isinstance(span_key, bool)
                and span_key > 0):
            if value_key + span_key > size.get(bound, 0):
                problems.append(
                    f"{label}: {key}+{bound} exceeds the room "
                    f"{size.get('w')}x{size.get('h')} bounds")
    return problems


def _validate_room_document(doc: Any, *, label: str = "room",
                            files: Optional[Set[str]] = None) -> List[str]:
    if not isinstance(doc, dict):
        return [f"{label}: must be an object"]
    problems: List[str] = []
    if doc.get("schema") != "qubit-office-room/1":
        problems.append(f"{label}.schema: must be 'qubit-office-room/1'")
    room_id = doc.get("id")
    if not isinstance(room_id, str) or not _ID_RE.fullmatch(room_id):
        problems.append(f"{label}.id: invalid room id")
    if not isinstance(doc.get("label"), str) or not doc["label"].strip():
        problems.append(f"{label}.label: must be a non-empty string")
    if doc.get("archetype") not in ROOM_ARCHETYPES:
        problems.append(f"{label}.archetype: unknown archetype {doc.get('archetype')!r}")
    problems.extend(_validate_int(doc.get("cell"), f"{label}.cell", minimum=1))
    size = doc.get("size")
    if not isinstance(size, dict):
        problems.append(f"{label}.size: must be an object")
        size = {}
    else:
        for key in ("w", "h"):
            problems.extend(_validate_int(size.get(key), f"{label}.size.{key}",
                                          minimum=1))
    floor = doc.get("floor")
    if not isinstance(floor, dict):
        problems.append(f"{label}.floor: must be an object")
    else:
        if not isinstance(floor.get("kind"), str) or not floor["kind"].strip():
            problems.append(f"{label}.floor.kind: must be a non-empty string")
        problems.extend(_source_label_problems(
            floor.get("sheet"), f"{label}.floor.sheet"))
        problems.extend(_validate_tile_ref(floor.get("tile"),
                                           f"{label}.floor.tile"))
    for section in ("walls", "openings"):
        entries = doc.get(section)
        if not isinstance(entries, list):
            problems.append(f"{label}.{section}: must be a list")
            continue
        for position, entry in enumerate(entries):
            entry_label = f"{label}.{section}[{position}]"
            if not isinstance(entry, dict):
                problems.append(f"{entry_label}: must be an object")
                continue
            if section == "walls":
                if entry.get("edge") not in ("north", "south", "east", "west"):
                    problems.append(
                        f"{entry_label}.edge: must be north, south, east, or west")
            else:
                if entry.get("kind") not in ("door", "window"):
                    problems.append(f"{entry_label}.kind: must be door or window")
                if entry.get("layer") != entry.get("kind"):
                    problems.append(
                        f"{entry_label}.layer: must match kind {entry.get('kind')!r}")
            problems.extend(_source_label_problems(
                entry.get("sheet"), f"{entry_label}.sheet"))
            problems.extend(_validate_tile_ref(entry.get("tile"),
                                               f"{entry_label}.tile"))
            problems.extend(_validate_room_rect(entry, entry_label, size))
    placements = doc.get("placements")
    if not isinstance(placements, list) or not placements:
        problems.append(f"{label}.placements: must be a non-empty list")
        placements = []
    seen: Set[str] = set()
    for position, entry in enumerate(placements):
        entry_label = f"{label}.placements[{position}]"
        if not isinstance(entry, dict):
            problems.append(f"{entry_label}: must be an object")
            continue
        placement_id = entry.get("id")
        if not isinstance(placement_id, str) or not placement_id:
            problems.append(f"{entry_label}.id: must be a non-empty string")
        elif placement_id in seen:
            problems.append(f"{entry_label}.id: duplicate id {placement_id!r}")
        else:
            seen.add(placement_id)
        if not isinstance(entry.get("kind"), str) or not entry["kind"].strip():
            problems.append(f"{entry_label}.kind: must be a non-empty string")
        if entry.get("layer") not in ROOM_LAYERS:
            problems.append(
                f"{entry_label}.layer: unknown layer {entry.get('layer')!r}")
        sprite = entry.get("sprite")
        if not isinstance(sprite, str) or not _ASSET_ID_RE.fullmatch(sprite):
            problems.append(f"{entry_label}.sprite: invalid sprite id")
        room_file = entry.get("file")
        if not isinstance(room_file, str) or not room_file:
            problems.append(f"{entry_label}.file: required")
        else:
            problems.extend(
                f"{entry_label}.file: {p}" for p in _path_problems(room_file))
            if os.path.splitext(room_file)[1].lower() != ".png":
                problems.append(f"{entry_label}.file: must be a .png file")
            if files is not None and room_file not in files:
                problems.append(
                    f"{entry_label}.file: {room_file!r} is not a declared file")
        tile = entry.get("tile")
        if not isinstance(tile, dict):
            problems.append(f"{entry_label}.tile: must be an object")
        else:
            for key in ("x", "y"):
                problems.extend(
                    _validate_int(tile.get(key), f"{entry_label}.tile.{key}"))
        footprint = entry.get("footprint")
        if not isinstance(footprint, dict):
            problems.append(f"{entry_label}.footprint: must be an object")
        else:
            for key in ("w", "h"):
                problems.extend(_validate_int(
                    footprint.get(key), f"{entry_label}.footprint.{key}",
                    minimum=1))
            if isinstance(tile, dict):
                problems.extend(_validate_room_rect(
                    {**tile, **footprint}, entry_label, size))
        if entry.get("anchor") not in ROOM_ANCHORS:
            problems.append(
                f"{entry_label}.anchor: unknown anchor {entry.get('anchor')!r}")
        problems.extend(_validate_int(entry.get("z"), f"{entry_label}.z"))
    return problems


def _validate_rooms_full(section: Any, files: Set[str]) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["rooms: must be an object"]
    problems: List[str] = []
    if section.get("schema") != 1:
        problems.append("rooms.schema: must be 1")
    index = section.get("index")
    if not isinstance(index, str) or not index:
        problems.append("rooms.index: required")
    else:
        problems.extend(f"rooms.index: {p}" for p in _path_problems(index))
        if os.path.splitext(index)[1].lower() != ".json":
            problems.append("rooms.index: must be a .json file")
        if files and index not in files:
            problems.append(f"rooms.index: {index!r} is not a declared file")
    problems.extend(_validate_int(section.get("count"), "rooms.count",
                                  minimum=1))
    archetypes = section.get("archetypes")
    if not isinstance(archetypes, list) or not archetypes:
        problems.append("rooms.archetypes: must be a non-empty list")
    else:
        for archetype in archetypes:
            if archetype not in ROOM_ARCHETYPES:
                problems.append(f"rooms.archetypes: unknown {archetype!r}")
        if len(set(archetypes)) != len(archetypes):
            problems.append("rooms.archetypes: must not repeat")
    return problems


def _validate_rooms_recipe(section: Any) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["rooms: must be an object"]
    problems: List[str] = []
    extra = sorted(set(section) - {"schema", "count", "archetypes"})
    if extra:
        problems.append(
            f"rooms: recipe metadata may not carry {', '.join(extra)} "
            "(art and layout are built locally)")
    if section.get("schema") != 1:
        problems.append("rooms.schema: must be 1")
    problems.extend(_validate_int(section.get("count"), "rooms.count",
                                  minimum=1))
    archetypes = section.get("archetypes")
    if not isinstance(archetypes, list) or not archetypes:
        problems.append("rooms.archetypes: must be a non-empty list")
    else:
        for archetype in archetypes:
            if archetype not in ROOM_ARCHETYPES:
                problems.append(f"rooms.archetypes: unknown {archetype!r}")
    return problems


def _validate_animations_full(section: Any, files: Set[str]) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["animations: must be an object"]
    problems: List[str] = []
    if section.get("schema") != 1:
        problems.append("animations.schema: must be 1")
    index = section.get("index")
    if not isinstance(index, str) or not index:
        problems.append("animations.index: required")
    else:
        problems.extend(f"animations.index: {p}" for p in _path_problems(index))
        if os.path.splitext(index)[1].lower() != ".json":
            problems.append("animations.index: must be a .json file")
        if files and index not in files:
            problems.append(
                f"animations.index: {index!r} is not a declared file")
    states = section.get("states")
    if not isinstance(states, list) or not states:
        problems.append("animations.states: must be a non-empty list")
    else:
        for state in states:
            if state not in ANIMATION_STATES:
                problems.append(f"animations.states: unknown {state!r}")
    directions = section.get("directions")
    if not isinstance(directions, list) or not directions:
        problems.append("animations.directions: must be a non-empty list")
    else:
        for direction in directions:
            if direction not in ANIMATION_DIRECTIONS:
                problems.append(f"animations.directions: unknown {direction!r}")
    return problems


def _validate_animations_recipe(section: Any) -> List[str]:
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["animations: must be an object"]
    problems: List[str] = []
    extra = sorted(set(section) - {
        "schema", "states", "directions", "cell", "portrait"})
    if extra:
        problems.append(
            f"animations: recipe metadata may not carry {', '.join(extra)} "
            "(frames are built locally)")
    if section.get("schema") != 1:
        problems.append("animations.schema: must be 1")
    states = section.get("states")
    if not isinstance(states, list) or not states:
        problems.append("animations.states: must be a non-empty list")
    else:
        for state in states:
            if state not in ANIMATION_STATES:
                problems.append(f"animations.states: unknown {state!r}")
    directions = section.get("directions")
    if not isinstance(directions, list) or not directions:
        problems.append("animations.directions: must be a non-empty list")
    else:
        for direction in directions:
            if direction not in ANIMATION_DIRECTIONS:
                problems.append(f"animations.directions: unknown {direction!r}")
    cell = section.get("cell")
    if not isinstance(cell, dict):
        problems.append("animations.cell: must be an object")
    else:
        for key in ("w", "h"):
            problems.extend(_validate_int(cell.get(key), f"animations.cell.{key}",
                                          minimum=1))
    portrait = section.get("portrait")
    if not isinstance(portrait, dict):
        problems.append("animations.portrait: must be an object")
    else:
        for key in ("cols", "rows"):
            problems.extend(_validate_int(
                portrait.get(key), f"animations.portrait.{key}", minimum=1))
        portrait_cell = portrait.get("cell")
        if not isinstance(portrait_cell, dict):
            problems.append("animations.portrait.cell: must be an object")
        else:
            for key in ("w", "h"):
                problems.extend(_validate_int(
                    portrait_cell.get(key),
                    f"animations.portrait.cell.{key}", minimum=1))
    return problems


def _validate_animations_document(doc: Any, *, label: str = "animations"
                                  ) -> List[str]:
    if not isinstance(doc, dict):
        return [f"{label}: must be an object"]
    problems: List[str] = []
    if doc.get("schema") != "qubit-office-character-animations/1":
        problems.append(
            f"{label}.schema: must be 'qubit-office-character-animations/1'")
    cell = doc.get("cell")
    if not isinstance(cell, dict):
        problems.append(f"{label}.cell: must be an object")
    else:
        for key in ("w", "h"):
            problems.extend(_validate_int(cell.get(key), f"{label}.cell.{key}",
                                          minimum=1))
    directions = doc.get("directions")
    if not isinstance(directions, list) or not directions:
        problems.append(f"{label}.directions: must be a non-empty list")
        directions = []
    else:
        for direction in directions:
            if direction not in ANIMATION_DIRECTIONS:
                problems.append(f"{label}.directions: unknown {direction!r}")
        if len(set(directions)) != len(directions):
            problems.append(f"{label}.directions: must not repeat")
    states = doc.get("states")
    if not isinstance(states, dict) or not states:
        problems.append(f"{label}.states: must be a non-empty object")
        states = {}
    for state, spec in states.items():
        state_label = f"{label}.states.{state}"
        if state not in ANIMATION_STATES:
            problems.append(f"{state_label}: unknown state")
            continue
        if not isinstance(spec, dict):
            problems.append(f"{state_label}: must be an object")
            continue
        problems.extend(_validate_int(spec.get("row"), f"{state_label}.row"))
        problems.extend(_validate_int(spec.get("frames"),
                                      f"{state_label}.frames", minimum=1))
        problems.extend(_validate_int(
            spec.get("frames_per_direction"),
            f"{state_label}.frames_per_direction", minimum=1))
        state_dirs = spec.get("directions", directions)
        if not isinstance(state_dirs, list) or not state_dirs:
            problems.append(f"{state_label}.directions: must be a non-empty list")
            state_dirs = []
        for direction in state_dirs:
            if direction not in directions:
                problems.append(
                    f"{state_label}.directions: {direction!r} not in {directions}")
        frames = spec.get("frames")
        per = spec.get("frames_per_direction")
        if (isinstance(frames, int) and not isinstance(frames, bool)
                and isinstance(per, int) and not isinstance(per, bool)
                and per > 0 and state_dirs
                and frames != per * len(state_dirs)):
            problems.append(
                f"{state_label}: frames {frames} != frames_per_direction "
                f"{per} x {len(state_dirs)} directions")
        fps = spec.get("fps")
        if (not isinstance(fps, (int, float)) or isinstance(fps, bool)
                or fps <= 0):
            problems.append(f"{state_label}.fps: must be a positive number")
        if not isinstance(spec.get("loop"), bool):
            problems.append(f"{state_label}.loop: must be a boolean")
    office = doc.get("office_states")
    if not isinstance(office, dict):
        problems.append(f"{label}.office_states: must be an object")
    else:
        for semantic in OFFICE_ANIMATION_STATES:
            target = office.get(semantic)
            if target not in states:
                problems.append(
                    f"{label}.office_states.{semantic}: must name a declared state")
    portrait = doc.get("portrait")
    if not isinstance(portrait, dict):
        problems.append(f"{label}.portrait: must be an object")
    else:
        pcell = portrait.get("cell")
        if not isinstance(pcell, dict):
            problems.append(f"{label}.portrait.cell: must be an object")
        else:
            for key in ("w", "h"):
                problems.extend(_validate_int(
                    pcell.get(key), f"{label}.portrait.cell.{key}", minimum=1))
        cols = portrait.get("cols")
        rows = portrait.get("rows")
        problems.extend(_validate_int(cols, f"{label}.portrait.cols", minimum=1))
        problems.extend(_validate_int(rows, f"{label}.portrait.rows", minimum=1))
        pdirs = portrait.get("directions")
        if not isinstance(pdirs, dict) or not pdirs:
            problems.append(f"{label}.portrait.directions: must be an object")
        else:
            for name, frame in pdirs.items():
                frame_label = f"{label}.portrait.directions.{name}"
                if not isinstance(frame, dict):
                    problems.append(f"{frame_label}: must be an object")
                    continue
                for key in ("row", "col"):
                    problems.extend(_validate_int(
                        frame.get(key), f"{frame_label}.{key}"))
                row = frame.get("row")
                col = frame.get("col")
                if (isinstance(rows, int) and isinstance(row, int)
                        and not (0 <= row < rows)):
                    problems.append(f"{frame_label}.row: out of bounds")
                if (isinstance(cols, int) and isinstance(col, int)
                        and not (0 <= col < cols)):
                    problems.append(f"{frame_label}.col: out of bounds")
        mapping = portrait.get("part_mapping")
        if not isinstance(mapping, dict) or not mapping:
            problems.append(f"{label}.portrait.part_mapping: must be an object")
        else:
            for part in ("skin", "eyes", "hair", "outfit"):
                if part not in mapping:
                    problems.append(
                        f"{label}.portrait.part_mapping.{part}: required")
            for part, value in mapping.items():
                if value not in ("skin", "eyes", "hair", "outfit", "accessory"):
                    problems.append(
                        f"{label}.portrait.part_mapping.{part}: unknown part")
    recipes = doc.get("recipes")
    if recipes is not None:
        if not isinstance(recipes, dict):
            problems.append(f"{label}.recipes: must be an object")
        else:
            for slug, recipe in recipes.items():
                recipe_label = f"{label}.recipes.{slug}"
                if not isinstance(slug, str) or not slug:
                    problems.append(f"{recipe_label}: invalid slug")
                    continue
                if not isinstance(recipe, dict):
                    problems.append(f"{recipe_label}: must be an object")
                    continue
                for part in ("skin", "eyes", "hair", "outfit"):
                    if part not in recipe:
                        problems.append(f"{recipe_label}.{part}: required")
                    else:
                        problems.extend(_validate_int(
                            recipe.get(part), f"{recipe_label}.{part}"))
                if "accessory" in recipe and recipe["accessory"] is not None:
                    problems.extend(_validate_int(
                        recipe.get("accessory"), f"{recipe_label}.accessory"))
                problems.extend(_validate_shared_recipe_frames(
                    recipe, recipe_label, portrait))
    return problems


def _validate_shared_recipe_frames(recipe: Mapping[str, Any], label: str,
                                   portrait: Any) -> List[str]:
    """A recipe must point at one body frame and one portrait frame.

    The point of the block is that the body and the portrait share the *same*
    part indices; the frame locators make that share explicit instead of
    letting two call sites pick independently.
    """
    problems: List[str] = []
    body = recipe.get("body")
    if not isinstance(body, dict):
        problems.append(f"{label}.body: must be an object")
    else:
        if body.get("state") not in ANIMATION_STATES:
            problems.append(f"{label}.body.state: unknown state")
        if body.get("direction") not in ANIMATION_DIRECTIONS:
            problems.append(f"{label}.body.direction: unknown direction")
        problems.extend(_validate_int(body.get("frame"),
                                      f"{label}.body.frame"))
    frame = recipe.get("portrait")
    if not isinstance(frame, dict):
        problems.append(f"{label}.portrait: must be an object")
    else:
        problems.extend(_validate_int(frame.get("row"),
                                      f"{label}.portrait.row"))
        problems.extend(_validate_int(frame.get("col"),
                                      f"{label}.portrait.col"))
        if isinstance(portrait, dict):
            rows, cols = portrait.get("rows"), portrait.get("cols")
            row, col = frame.get("row"), frame.get("col")
            if (isinstance(rows, int) and isinstance(row, int)
                    and not (0 <= row < rows)):
                problems.append(f"{label}.portrait.row: out of bounds")
            if (isinstance(cols, int) and isinstance(col, int)
                    and not (0 <= col < cols)):
                problems.append(f"{label}.portrait.col: out of bounds")
    return problems


# --------------------------------------------------------------------------
# Manifest / recipe / theme validation
# --------------------------------------------------------------------------
def _looks_like_path(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered.startswith(("/", "~/", "~\\", "\\\\")):
        return True
    if re.match(r"^[a-z]:[\\/]", lowered):
        return True
    if "/home/" in lowered or "\\users\\" in lowered:
        return True
    if re.search(r"(^|[\\/])\.\.([\\/]|$)", value):
        return True
    return False


def _validate_common(doc: Any, require_files: bool) -> List[str]:
    if not isinstance(doc, dict):
        return ["manifest: must be an object"]
    problems: List[str] = []

    if doc.get("format") != FORMAT:
        problems.append(f"format: must be {FORMAT!r}")
    schema = doc.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != SCHEMA:
        problems.append(f"schema: must be {SCHEMA}")

    theme_id = doc.get("id")
    if not isinstance(theme_id, str) or not _ID_RE.fullmatch(theme_id):
        problems.append("id: must match ^[a-z0-9][a-z0-9-]{0,39}$")

    label = doc.get("label")
    if not isinstance(label, str) or not label.strip():
        problems.append("label: must be a non-empty string")

    version = doc.get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        problems.append("version: must be a non-empty safe version string")

    for field_name in ("credit", "license"):
        value = doc.get(field_name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field_name}: must be a non-empty string")

    source = doc.get("source")
    if source is not None:
        if not isinstance(source, str) or not source.strip():
            problems.append("source: must be a non-empty description")
        elif _looks_like_path(source):
            problems.append("source: must be a description, not a filesystem path")

    template_version = doc.get("template_version")
    if (not isinstance(template_version, int)
            or isinstance(template_version, bool)
            or template_version <= 0):
        problems.append("template_version: must be a positive integer")

    tokens = doc.get("tokens")
    if not isinstance(tokens, dict):
        problems.append("tokens: must be an object")
    else:
        problems.extend(validate_tokens(tokens))

    if not require_files:
        problems.extend(_validate_slices(doc.get("slices"), None))
        problems.extend(_validate_surfaces_recipe(doc.get("surfaces")))
        problems.extend(_validate_assets_recipe(doc.get("assets")))
        problems.extend(_validate_characters_recipe(doc.get("characters")))
        problems.extend(_validate_furniture_recipe(doc.get("furniture")))
        problems.extend(_validate_ui_recipe(doc.get("ui")))
        problems.extend(_validate_rooms_recipe(doc.get("rooms")))
        problems.extend(_validate_animations_recipe(doc.get("animations")))
    problems.extend(_validate_parts(doc.get("parts")))
    problems.extend(_validate_agents(doc.get("agents"), doc.get("parts")))

    if not require_files:
        if doc.get("files"):
            problems.append("recipe: must not declare files (art is built locally)")
        if doc.get("portraits") or doc.get("bodies"):
            problems.append("recipe: must not declare atlases (art is built locally)")
    return problems


def validate_recipe(recipe: Any) -> List[str]:
    """Validate the shareable ``theme.recipe.json`` (no files, no art)."""
    return _validate_common(recipe, require_files=False)


def validate_manifest(manifest: Any, root: Optional[Path] = None) -> List[str]:
    """Validate a full pack manifest. ``root`` enables existence/hash checks."""
    problems = _validate_common(manifest, require_files=True)
    if not isinstance(manifest, dict):
        return problems
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        problems.append("files: must be a non-empty object")
        files = {}
    else:
        problems.extend(_validate_files(files, Path(root) if root else None,
                                        manifest.get("limits")))
    declared = set(files) if isinstance(files, dict) else set()
    problems.extend(_validate_slices(manifest.get("slices"), declared))
    problems.extend(_validate_surfaces_full(manifest.get("surfaces"), declared))
    problems.extend(_validate_assets_full(manifest.get("assets"), declared))
    problems.extend(_validate_characters_full(manifest.get("characters"), declared))
    problems.extend(_validate_furniture_full(manifest.get("furniture"), declared))
    problems.extend(_validate_ui_full(manifest.get("ui"), declared))
    problems.extend(_validate_rooms_full(manifest.get("rooms"), declared))
    problems.extend(_validate_animations_full(manifest.get("animations"), declared))
    for section_name in ("portraits", "bodies"):
        problems.extend(_validate_asset_section(
            section_name, manifest.get(section_name), declared))
    return problems


def _validate_index(root: Path, section_name: str, section: Any) -> List[str]:
    if not isinstance(section, dict):
        return []
    relative = section.get("index")
    if not isinstance(relative, str) or not relative:
        return []
    path = _inside_root(root, relative)
    if path is None or not path.is_file():
        return []
    problems: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{section_name} index: unreadable ({exc})"]
    if "/home/" in text or "\\" in text or "<script" in text.lower():
        problems.append(f"{section_name} index: must not contain paths or scripts")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return problems + [f"{section_name} index: invalid JSON ({exc})"]
    if not isinstance(data, dict):
        return problems + [f"{section_name} index: must be an object"]
    cols = section.get("cols")
    rows = section.get("rows")
    for key in ("size", "cols", "rows"):
        if key in data and isinstance(cols, int) and key in section:
            if data.get(key) != section.get(key):
                problems.append(
                    f"{section_name} index: {key} {data.get(key)!r} != "
                    f"{section.get(key)!r}")
    cells = data.get("cells")
    if cells is None:
        return problems
    if not isinstance(cells, dict):
        return problems + [f"{section_name} index: cells must be an object"]
    for slug, cell in cells.items():
        if not isinstance(slug, str) or not slug:
            problems.append(f"{section_name} index: invalid slug {slug!r}")
            continue
        if (not isinstance(cell, (list, tuple)) or len(cell) != 2
                or any(not isinstance(v, int) or isinstance(v, bool) for v in cell)):
            problems.append(f"{section_name} index: {slug} cell must be [x, y]")
            continue
        x, y = cell
        if not isinstance(cols, int) or not isinstance(rows, int):
            continue
        if not (0 <= x < cols and 0 <= y < rows):
            problems.append(
                f"{section_name} index: {slug} cell [{x}, {y}] out of bounds "
                f"({cols}x{rows})")
    return problems


def _validate_agents_file(root: Path, files: Mapping[str, Any],
                          manifest: Mapping[str, Any]) -> List[str]:
    if "agents.json" not in files:
        return []
    path = _inside_root(root, "agents.json")
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"agents.json: invalid JSON ({exc})"]
    if not isinstance(data, dict):
        return ["agents.json: must be an object"]
    problems: List[str] = []
    declared = data.get("templateVersion")
    expected = manifest.get("template_version")
    if (isinstance(declared, int) and not isinstance(declared, bool)
            and isinstance(expected, int) and declared != expected):
        problems.append(
            f"agents.json: templateVersion {declared} != template_version {expected}")
    parts = data.get("parts", manifest.get("parts"))
    problems.extend(_validate_agents(data.get("recipes"), parts))
    return problems


def _validate_rooms_index(root: Path, files: Mapping[str, Any],
                          section: Any) -> List[str]:
    if not isinstance(section, dict):
        return []
    relative = section.get("index")
    if not isinstance(relative, str) or not relative:
        return []
    path = _inside_root(root, relative)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"rooms.index: invalid JSON ({exc})"]
    if not isinstance(data, dict):
        return ["rooms.index: must be an object"]
    problems: List[str] = []
    if data.get("schema") != 1:
        problems.append("rooms.index: schema must be 1")
    rooms = data.get("rooms")
    if not isinstance(rooms, list):
        return problems + ["rooms.index: rooms must be a list"]
    count = section.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count != len(rooms):
        problems.append(
            f"rooms.count: {count} != {len(rooms)} index entries")
    archetypes = section.get("archetypes")
    declared = set(archetypes) if isinstance(archetypes, list) else set()
    seen: Set[str] = set()
    for position, entry in enumerate(rooms):
        label = f"rooms.index.rooms[{position}]"
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be an object")
            continue
        room_id = entry.get("id")
        if not isinstance(room_id, str) or not _ID_RE.fullmatch(room_id):
            problems.append(f"{label}.id: invalid room id")
        elif room_id in seen:
            problems.append(f"{label}.id: duplicate {room_id!r}")
        else:
            seen.add(room_id)
        archetype = entry.get("archetype")
        if archetype not in ROOM_ARCHETYPES:
            problems.append(f"{label}.archetype: unknown {archetype!r}")
        elif declared and archetype not in declared:
            problems.append(
                f"{label}.archetype: {archetype!r} not in rooms.archetypes")
        room_file = entry.get("file")
        if not isinstance(room_file, str) or not room_file:
            problems.append(f"{label}.file: required")
            continue
        problems.extend(f"{label}.file: {p}" for p in _path_problems(room_file))
        if room_file not in files:
            problems.append(f"{label}.file: {room_file!r} is not a declared file")
        room_path = _inside_root(root, room_file)
        if room_path is None or not room_path.is_file():
            continue
        try:
            document = json.loads(room_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(f"{label}: invalid JSON ({exc})")
            continue
        problems.extend(
            f"{label}: {problem}"
            for problem in _validate_room_document(
                document, label=label, files=set(files)))
        if isinstance(document, dict) and document.get("id") != room_id:
            problems.append(f"{label}: document id does not match the index")
    return problems


def _validate_animations_index(root: Path, files: Mapping[str, Any],
                               section: Any) -> List[str]:
    if not isinstance(section, dict):
        return []
    relative = section.get("index")
    if not isinstance(relative, str) or not relative:
        return []
    path = _inside_root(root, relative)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"animations.index: invalid JSON ({exc})"]
    problems = _validate_animations_document(data, label="animations.index")
    if isinstance(data, dict):
        states = data.get("states")
        declared = section.get("states")
        if isinstance(states, dict) and isinstance(declared, list):
            missing = [state for state in declared if state not in states]
            if missing:
                problems.append(
                    f"animations.states: {', '.join(missing)} missing from the index")
    return problems


def validate_theme(root: Any) -> List[str]:
    """Validate a locally built full pack at ``root``."""
    root = Path(root)
    if not root.is_dir():
        return [f"theme root: {root} is not a directory"]
    theme_json = root / "theme.json"
    if not theme_json.is_file():
        return ["theme.json: missing"]
    try:
        manifest = json.loads(theme_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"theme.json: invalid JSON ({exc})"]

    problems = validate_manifest(manifest, root=root)
    if not isinstance(manifest, dict):
        return problems
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    for section_name in ("portraits", "bodies"):
        problems.extend(_validate_index(root, section_name,
                                        manifest.get(section_name)))
    problems.extend(_validate_agents_file(root, files, manifest))
    assets = manifest.get("assets")
    if isinstance(assets, dict) and isinstance(assets.get("categories"), dict):
        for category, config in assets["categories"].items():
            if isinstance(config, dict):
                problems.extend(_validate_assets_index(
                    root, files, category, config))
    problems.extend(_validate_rooms_index(root, files, manifest.get("rooms")))
    problems.extend(_validate_animations_index(
        root, files, manifest.get("animations")))
    return problems


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------
def _theme_cache_key(root: Path) -> Optional[tuple]:
    theme_json = root / "theme.json"
    try:
        stat = theme_json.stat()
    except OSError:
        return None
    return (str(root.resolve()), stat.st_mtime_ns, stat.st_size)


def load_theme(root: Any) -> Theme:
    """Load and validate one pack. Raises :class:`ThemeError` when invalid."""
    root = Path(root)
    key = _theme_cache_key(root)
    if key is not None:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached

    problems = validate_theme(root)
    if problems:
        raise ThemeError(problems)

    manifest = json.loads((root / "theme.json").read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    agents = manifest.get("agents") if isinstance(manifest.get("agents"), dict) else {}
    if "agents.json" in files:
        try:
            agents_doc = json.loads((root / "agents.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            agents_doc = None
        if isinstance(agents_doc, dict) and isinstance(agents_doc.get("recipes"), dict):
            agents = agents_doc["recipes"]

    theme = Theme(
        root=root,
        id=manifest["id"],
        label=manifest["label"],
        version=manifest["version"],
        credit=manifest["credit"],
        license=manifest["license"],
        source=manifest.get("source", ""),
        template_version=manifest["template_version"],
        tokens=dict(manifest.get("tokens") or {}),
        slices=dict(manifest.get("slices") or {}),
        portraits=dict(manifest.get("portraits") or {}),
        bodies=dict(manifest.get("bodies") or {}),
        surfaces=dict(manifest.get("surfaces") or {}),
        assets=dict(manifest.get("assets") or {}),
        characters=dict(manifest.get("characters") or {}),
        furniture=dict(manifest.get("furniture") or {}),
        ui=dict(manifest.get("ui") or {}),
        rooms=dict(manifest.get("rooms") or {}),
        animations=dict(manifest.get("animations") or {}),
        files=dict(files),
        agents=dict(agents),
    )
    if key is not None:
        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = theme
    return theme


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------
def _safe_version(version: str) -> str:
    return version if isinstance(version, str) and _VERSION_RE.fullmatch(version) else ""


def _classic_payload(reason: Optional[str]) -> Dict[str, Any]:
    return {
        "id": CLASSIC,
        "label": "Classic",
        "version": "",
        "credit": "",
        "license": "",
        "active": False,
        "assetBase": "",
        "atlas": None,
        "index": None,
        "bodies": {"atlas": None, "index": None, "size": None, "frame": None},
        "tokens": {},
        "slices": {},
        "surfaces": None,
        "assets": None,
        "characters": None,
        "furniture": {},
        "ui": {"slices": {}},
        "reason": reason or "",
    }


def _active_payload(theme: Theme) -> Dict[str, Any]:
    base = f"{ASSET_BASE}/{theme.id}"
    suffix = f"?v={_safe_version(theme.version)}" if _safe_version(theme.version) else ""

    def url(relative: Optional[str]) -> Optional[str]:
        return f"{base}/{relative}{suffix}" if relative else None

    portraits = theme.portraits or {}
    bodies = theme.bodies or {}
    raw_surfaces = theme.surfaces or {}
    surfaces_payload: Optional[Dict[str, Any]] = None
    if raw_surfaces:
        floor = raw_surfaces.get("floor")
        surfaces_payload = {
            "cell": raw_surfaces.get("cell"),
            "floor": {
                kind: url(relative)
                for kind, relative in (floor or {}).items()
                if isinstance(relative, str) and relative
            },
        }
    raw_assets = theme.assets or {}
    assets_payload: Optional[Dict[str, Any]] = None
    if raw_assets:
        categories = raw_assets.get("categories") or {}
        assets_payload = {
            "schema": raw_assets.get("schema"),
            "categories": {
                category: {
                    "index": url(config.get("index")),
                    "count": config.get("count"),
                    "kinds": dict(config.get("kinds") or {}),
                }
                for category, config in categories.items()
                if isinstance(config, dict)
            },
        }
    return {
        "id": theme.id,
        "label": theme.label,
        "version": theme.version,
        "credit": theme.credit,
        "license": theme.license,
        "active": True,
        "templateVersion": theme.template_version,
        "assetBase": base,
        "atlas": url(portraits.get("atlas")),
        "index": url(portraits.get("index")),
        "bodies": {
            "atlas": url(bodies.get("atlas")),
            "index": url(bodies.get("index")),
            "size": bodies.get("size"),
            "frame": bodies.get("frame"),
        },
        "tokens": dict(theme.tokens),
        "slices": dict(theme.slices),
        "surfaces": surfaces_payload,
        "assets": assets_payload,
        "characters": _characters_payload(theme),
        "furniture": _furniture_payload(theme),
        "ui": _ui_payload(theme),
        "reason": "",
    }


def _characters_payload(theme: Theme) -> Optional[Dict[str, Any]]:
    """The body atlas as the in-scene character source, when the pack has one.

    A pack may declare an explicit ``characters`` block; otherwise the proven
    ``bodies`` atlas is reused (same cells, same frame size), which is what the
    builder emits. Classic/absent packs return ``None`` so the renderer keeps
    its procedural characters.
    """
    section = dict(theme.characters or {})
    if not section:
        bodies = theme.bodies or {}
        if not bodies.get("atlas"):
            return None
        section = {
            "atlas": bodies.get("atlas"),
            "cell": bodies.get("frame"),
            "cols": bodies.get("cols"),
            "rows": bodies.get("rows"),
        }
    atlas = section.get("atlas")
    if not isinstance(atlas, str) or not atlas:
        return None
    base = f"{ASSET_BASE}/{theme.id}"
    suffix = f"?v={_safe_version(theme.version)}" if _safe_version(theme.version) else ""
    payload: Dict[str, Any] = {
        "atlas": f"{base}/{atlas}{suffix}",
        "cell": dict(section.get("cell") or {}),
        "cols": section.get("cols"),
        "rows": section.get("rows"),
        "anchor": dict(section.get("anchor") or {}),
    }
    if isinstance(section.get("count"), int):
        payload["count"] = section["count"]
    if isinstance(section.get("scale"), (int, float)):
        payload["scale"] = section["scale"]
    return payload


def _furniture_payload(theme: Theme) -> Dict[str, Any]:
    """Per-kind furniture image URLs (only the kinds the pack replaces)."""
    base = f"{ASSET_BASE}/{theme.id}"
    suffix = f"?v={_safe_version(theme.version)}" if _safe_version(theme.version) else ""
    out: Dict[str, Any] = {}
    for kind, descriptor in (theme.furniture or {}).items():
        if not isinstance(descriptor, dict):
            continue
        src = descriptor.get("src")
        if not isinstance(src, str) or not src:
            continue
        entry: Dict[str, Any] = {"src": f"{base}/{src}{suffix}"}
        for key in ("ax", "ay", "scale"):
            if key in descriptor:
                entry[key] = descriptor[key]
        out[kind] = entry
    return out


def _ui_payload(theme: Theme) -> Dict[str, Any]:
    """Modern UI 9-slice backgrounds for Habblaud's own panels/buttons."""
    base = f"{ASSET_BASE}/{theme.id}"
    suffix = f"?v={_safe_version(theme.version)}" if _safe_version(theme.version) else ""
    slices = (theme.ui or {}).get("slices")
    out: Dict[str, Any] = {}
    if isinstance(slices, dict):
        for name, descriptor in slices.items():
            if not isinstance(descriptor, dict):
                continue
            src = descriptor.get("src")
            if not isinstance(src, str) or not src:
                continue
            out[name] = {
                "src": f"{base}/{src}{suffix}",
                "slice": list(descriptor.get("slice") or []),
                "width": descriptor.get("width"),
                "repeat": descriptor.get("repeat"),
            }
    return {"slices": out}


def theme_payload(cfg: Any, requested: Optional[str] = None) -> Dict[str, Any]:
    """The trimmed, validated payload for the active theme (or classic)."""
    raw = _raw_theme_id(cfg, requested)
    if not raw or raw == CLASSIC:
        return _classic_payload(None)
    if not _ID_RE.fullmatch(raw):
        return _classic_payload(f"invalid theme id {raw!r}")
    theme_dir = find_theme(raw)
    if theme_dir is None:
        return _classic_payload(f"theme {raw!r} not found")
    try:
        theme = load_theme(theme_dir)
    except ThemeError as exc:
        return _classic_payload("theme invalid: " + "; ".join(exc.problems))
    return _active_payload(theme)


def _script_json(value: Any) -> str:
    """JSON that is safe to inline inside a ``<script>`` element.

    JSON itself may contain ``</script>`` or ``<!--`` in pack-provided text
    (labels, credit, tokens). Escaping ``<``/``>``/``&`` and the JS line
    separators means the string cannot close the element or start an HTML
    comment, while ``json.loads``/JS still parse it to the same value.
    """
    return (json.dumps(value)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def office_document_script(cfg: Any, requested: Optional[str] = None) -> str:
    """The office app document's ``window.__QUBIT_OFFICE_THEME__`` global.

    The theme payload is resolved once (env beats ``?theme=`` beats config) and
    inlined for the same-origin Habblaud iframe. It is always emitted — classic
    is a valid payload — because the client needs to distinguish "no theme" from
    "theme not loaded yet". Pack text is JSON-encoded and inline-script escaped.
    """
    return ("<script>window.__QUBIT_OFFICE_THEME__=%s;</script>"
            % _script_json(theme_payload(cfg, requested)))


# --------------------------------------------------------------------------
# Atlas cells and file serving
# --------------------------------------------------------------------------
def _index_cells(theme: "Theme", kind: str) -> Dict[str, List[int]]:
    """The ``cells`` map from a pack's portraits/bodies index (cached)."""
    section = theme.portraits if kind == "portraits" else theme.bodies
    relative = section.get("index") if isinstance(section, Mapping) else None
    if not isinstance(relative, str) or not relative:
        return {}
    path = _inside_root(theme.root, relative)
    if path is None or not path.is_file():
        return {}
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    raw_cells = data.get("cells") if isinstance(data, dict) else None
    cells: Dict[str, List[int]] = {}
    if isinstance(raw_cells, dict):
        for slug, cell in raw_cells.items():
            if (not isinstance(slug, str) or not slug
                    or not isinstance(cell, (list, tuple)) or len(cell) != 2
                    or any(not isinstance(v, int) or isinstance(v, bool)
                           for v in cell)):
                continue
            cells[slug] = [int(cell[0]), int(cell[1])]
    with _CACHE_LOCK:
        if len(_INDEX_CACHE) >= _CACHE_MAX:
            _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
        _INDEX_CACHE[key] = cells
    return cells


def cell_for(cfg: Any, slug: str, kind: str = "portraits",
             requested: Optional[str] = None) -> Optional[List[int]]:
    """The ``[col, row]`` atlas cell for ``slug`` in the active theme.

    Returns ``None`` for classic, an unknown/invalid theme, an unknown slug, an
    unsafe slug, or a ``kind`` other than ``portraits``/``bodies``. The parsed
    index is cached per file stamp, so this is cheap enough to call once per
    seat row.
    """
    if kind not in ("portraits", "bodies"):
        return None
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        return None
    theme_id = active_theme_id(cfg, requested)
    if theme_id == CLASSIC:
        return None
    theme_dir = find_theme(theme_id)
    if theme_dir is None:
        return None
    try:
        theme = load_theme(theme_dir)
    except ThemeError:
        return None
    cell = _index_cells(theme, kind).get(slug)
    return list(cell) if cell is not None else None


def theme_asset_path(theme_id: str, relative: str) -> Optional[Path]:
    """Resolve one theme-relative asset to a safe real file, or ``None``.

    The only reader used by the ``/plugins/qubit-office/themes/<id>/<rel>``
    route. It is deliberately narrower than :func:`find_theme`:

    * the pack must live under :func:`user_theme_root`
      (``$QUBIT_HOME/office/themes/<id>``) — a bundled/source-root pack is
      **never** served, even though the loader may still see it;
    * the pack must validate (:func:`load_theme`), so an invalid pack exposes
      nothing;
    * only a file declared in the manifest's ``files`` map is served, so
      ``theme.json`` itself and undeclared files are 404;
    * the path must be relative, suffix-allowlisted, symlink-free, confined to
      the pack root, and a regular file.

    ``None`` means "answer 404". Nothing is ever copied into the built
    dashboard.
    """
    if not isinstance(theme_id, str) or not _ID_RE.fullmatch(theme_id):
        return None
    if not isinstance(relative, str) or not relative:
        return None
    relative = relative.replace("\\", "/")
    if "\x00" in relative or relative.startswith("/"):
        return None
    segments = relative.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return None
    if relative == "theme.json":
        return None
    suffix = os.path.splitext(segments[-1])[1].lower()
    if suffix not in ALLOWED_SUFFIXES:
        return None
    theme_dir = user_theme_root() / theme_id
    if not (theme_dir / "theme.json").is_file():
        return None
    try:
        theme = load_theme(theme_dir)
    except (ThemeError, OSError, RuntimeError):
        return None
    if relative not in theme.files:
        return None
    if _symlink_component(theme_dir, relative) is not None:
        return None
    candidate = _inside_root(theme_dir, relative)
    if candidate is None or not candidate.is_file():
        return None
    return candidate


# --------------------------------------------------------------------------
# Token -> CSS
# --------------------------------------------------------------------------
def _safe_asset_base(asset_base: str) -> str:
    if not isinstance(asset_base, str) or not asset_base.strip():
        raise ValueError("asset_base: must be a non-empty string")
    if any(ch in asset_base for ch in "\"'()\\;{}@ \t\r\n"):
        raise ValueError("asset_base: unsafe characters")
    # The base is a same-origin *path* (``/plugins/...``). A protocol-relative
    # ``//host/...`` or a scheme'd absolute URL would turn every slice src into
    # a remote fetch, so both are refused outright.
    if asset_base.startswith("//"):
        raise ValueError("asset_base: must be a path, not a network-path reference")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", asset_base):
        raise ValueError("asset_base: must be a path, not an absolute URL")
    return asset_base.rstrip("/")


def token_css(tokens: Any, slices: Any, asset_base: str, version: str,
              files: Optional[Iterable[str]] = None, *,
              selector: str = "html body .ui-root") -> str:
    """Emit the allowlisted ``.ui-root`` variables and validated slices.

    Raises :class:`ValueError` rather than emitting anything unsafe. Token
    rules contain no layout, pointer, z-index, ``@layer``, or ``!important``
    declarations. A slice rule is the one deliberate exception: it reserves a
    border (``border-width``) equal to its ``border-image-width`` so the image
    is painted into exactly the border box it was authored for. Pass ``files``
    (the pack's declared file keys) to also require every slice ``src`` to be
    declared.

    ``selector`` is the scope the variables are emitted under. It is a
    constant owned by this module, never pack data: the office app document
    uses ``html body .ui-root`` (Habblaud's own root), and the dashboard host
    uses ``:root[data-office-theme="<id>"]`` so our docks inherit the same
    validated tokens without duplicating the semantic mapping in TypeScript.
    """
    tokens = tokens or {}
    slices = slices or {}
    token_problems = validate_tokens(tokens)
    if token_problems:
        raise ValueError("; ".join(token_problems))
    declared = set(files) if files is not None else None
    slice_problems = _validate_slices(slices, declared)
    if slice_problems:
        raise ValueError("; ".join(slice_problems))

    base = _safe_asset_base(asset_base)
    safe_version = _safe_version(version)
    if version and not safe_version:
        raise ValueError("version: unsafe characters")
    suffix = f"?v={safe_version}" if safe_version else ""

    lines: List[str] = [f"{selector} {{"]
    emitted: Set[str] = set()
    for token, variable in TOKEN_MAP.items():
        if token not in tokens or variable in emitted:
            continue
        lines.append(f"  {variable}: {tokens[token]};")
        emitted.add(variable)
    lines.append("}")

    for name, descriptor in slices.items():
        cell = descriptor["slice"]
        repeat = descriptor["repeat"]
        src = f"{base}/{descriptor['src']}{suffix}"
        parts = _SLICE_STATE_SELECTORS.get(name)
        if parts is None:
            targets = [f'{selector} [data-office-slice="{name}"]']
        else:
            targets = [f"{selector} {part}" for part in parts]
        lines.append(",\n".join(targets) + " {")
        lines.append(
            f'  border-image: url("{src}") '
            f"{cell[0]} {cell[1]} {cell[2]} {cell[3]} {repeat};")
        # The themed border and the slice's paint width are the SAME value: a
        # border-image stretched over a narrower border would overlap content,
        # and over a wider one would leave a gap.
        lines.append("  border-style: solid;")
        lines.append(f"  border-width: {descriptor['width']};")
        lines.append(f"  border-image-width: {descriptor['width']};")
        lines.append("}")
    return "\n".join(lines) + "\n"


def theme_style_html(cfg: Any, requested: Optional[str] = None) -> str:
    """One ``<style data-office-theme="<id>">`` for the office app document.

    Returns the empty string for ``classic``, an unknown/invalid theme, or an
    active pack that carries no tokens. The CSS is generated by
    :func:`token_css` from the **validated payload** (never pack-provided CSS),
    with no slices: the in-iframe scope only ever carries the allowlisted
    variables, so the document contains no ``url(``/``@import`` it could use to
    fetch anything. Pack text is never interpolated — ``cfg``/``requested`` are
    resolved through :func:`theme_payload` and the id is re-checked here.
    """
    payload = theme_payload(cfg, requested)
    if not payload.get("active"):
        return ""
    tokens = payload.get("tokens") or {}
    theme_id = payload.get("id") or ""
    if not tokens or not _ID_RE.fullmatch(theme_id):
        return ""
    try:
        css = token_css(tokens, {}, ASSET_BASE, payload.get("version") or "")
    except ValueError:
        return ""
    return f'<style data-office-theme="{theme_id}">\n{css}</style>'


def host_token_css(tokens: Any, theme_id: str, version: str,
                   slices: Any = None,
                   asset_base: Optional[str] = None) -> str:
    """The same validated tokens, scoped to the dashboard host element.

    Served as the host's ``theme.css`` link so the semantic-token -> CSS
    variable mapping stays in ONE place (this module) instead of being mirrored
    in TypeScript. ``theme_id`` must be a validated id because it is
    interpolated into the selector; the tokens and optional slice descriptors
    go through the same :func:`token_css` validators. A slice is emitted as
    ``[data-office-slice="<name>"]`` inside the host scope, so only the Qubit
    shell's own elements (docks, agent card, buttons) ever receive a
    ``border-image``; the vendored app document is never targeted here.
    """
    if not isinstance(theme_id, str) or not _ID_RE.fullmatch(theme_id):
        raise ValueError("theme id: invalid")
    slices = slices or {}
    if (not isinstance(tokens, dict) or not tokens) and not slices:
        return ""
    base = asset_base if asset_base is not None else ASSET_BASE
    return token_css(tokens or {}, slices, base, version,
                     selector=f':root[data-office-theme="{theme_id}"]')
