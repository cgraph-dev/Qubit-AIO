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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

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
ALLOWED_SUFFIXES: Set[str] = {".png", ".jpg", ".jpeg", ".webp", ".json", ".txt"}
WINDOWS_RESERVED: Set[str] = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

ASSET_BASE = "/plugins/qubit-office/themes"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_TOKEN_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
_LENGTH_RE = re.compile(r"^(0|[1-9][0-9]{0,3})(px|rem|em)$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SLICE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_SLICE_WIDTH_RE = re.compile(r"^[1-9][0-9]{0,3}px$")
_SLICE_REPEAT = {"stretch", "round", "repeat"}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

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
    files: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    agents: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Roots and config access
# --------------------------------------------------------------------------
def theme_roots() -> List[Path]:
    """Where a theme may live, most specific first."""
    candidates = [
        qubit_home() / "office" / "themes",
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


def _contrast_problems(tokens: Mapping[str, Any]) -> List[str]:
    problems: List[str] = []
    effective = _effective_token_map(tokens)
    surface = effective.get("--ui-bg")
    raised = effective.get("--ui-bg-solid")
    hover = effective.get("--ui-bg-hover")
    text = effective.get("--ui-text")
    muted = effective.get("--ui-muted")

    pairs = [
        ("--ui-text", text, "--ui-bg", surface, 4.5),
        ("--ui-muted", muted, "--ui-bg", surface, 3.0),
        ("--ui-text", text, "--ui-bg-solid", raised, 4.5),
        ("--ui-muted", muted, "--ui-bg-hover", hover, 3.0),
    ]
    for first_name, first, second_name, second, floor in pairs:
        if first is None or second is None:
            continue
        if _hex_rgb(first) is None or _hex_rgb(second) is None:
            continue
        ratio = contrast_ratio(first, second)
        if ratio + 1e-9 < floor:
            problems.append(
                f"token contrast: {first_name} vs {second_name} is "
                f"{ratio:.2f}:1; needs >= {floor}:1")

    if surface is not None and _hex_rgb(surface) is not None:
        for variable in STATUS_TOKENS:
            value = effective.get(variable)
            if value is None or _hex_rgb(value) is None:
                continue
            ratio = contrast_ratio(value, surface)
            if ratio + 1e-9 < 3.0:
                problems.append(
                    f"token contrast: {variable} vs --ui-bg is "
                    f"{ratio:.2f}:1; needs >= 3.0:1")
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


def _validate_files(files: Any, root: Optional[Path],
                    limits: Any) -> List[str]:
    if not isinstance(files, dict) or not files:
        return ["files: must be a non-empty object"]
    problems: List[str] = _duplicate_problems(files.keys())
    total_bytes = 0
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
        else:
            total_bytes += size
        if root is None or path_problems:
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
            actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if isinstance(sha, str) and _SHA256_RE.fullmatch(sha) and actual_sha.lower() != sha.lower():
                problems.append(f"file {relative}: sha256 mismatch")
        except OSError as exc:
            problems.append(f"file {relative}: unreadable ({exc})")

    if limits is not None and not isinstance(limits, dict):
        problems.append("limits: must be an object")
    if isinstance(limits, dict):
        for key in ("max_files", "max_total_bytes"):
            if key not in limits:
                continue
            value = limits[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                problems.append(f"limits.{key}: must be a positive integer")
        max_files = limits.get("max_files")
        if isinstance(max_files, int) and not isinstance(max_files, bool):
            if len(files) > max_files:
                problems.append(
                    f"files: {len(files)} entries exceed max_files={max_files}")
        max_total = limits.get("max_total_bytes")
        if isinstance(max_total, int) and not isinstance(max_total, bool):
            if total_bytes > max_total:
                problems.append(
                    f"files: {total_bytes} bytes exceed max_total_bytes={max_total}")
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
        "reason": reason or "",
    }


def _active_payload(theme: Theme) -> Dict[str, Any]:
    base = f"{ASSET_BASE}/{theme.id}"
    suffix = f"?v={_safe_version(theme.version)}" if _safe_version(theme.version) else ""

    def url(relative: Optional[str]) -> Optional[str]:
        return f"{base}/{relative}{suffix}" if relative else None

    portraits = theme.portraits or {}
    bodies = theme.bodies or {}
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
        "reason": "",
    }


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
    route. ``None`` means "answer 404": the id is not a local pack, the path is
    empty/absolute/``.``/``..``-bearing, has a symlinked component, resolves
    outside the pack root, carries a suffix outside :data:`ALLOWED_SUFFIXES`, or
    is not a regular file. The pack is resolved through :func:`find_theme`, so
    only packs under :func:`theme_roots` (the operator home first) are served —
    nothing is ever copied into the built dashboard.
    """
    theme_dir = find_theme(theme_id)
    if theme_dir is None:
        return None
    if not isinstance(relative, str) or not relative:
        return None
    relative = relative.replace("\\", "/")
    if "\x00" in relative or relative.startswith("/"):
        return None
    segments = relative.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return None
    suffix = os.path.splitext(segments[-1])[1].lower()
    if suffix not in ALLOWED_SUFFIXES:
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
    return asset_base.rstrip("/")


def token_css(tokens: Any, slices: Any, asset_base: str, version: str,
              files: Optional[Iterable[str]] = None) -> str:
    """Emit the allowlisted ``.ui-root`` variables and validated slices.

    Raises :class:`ValueError` rather than emitting anything unsafe. The
    returned CSS contains no layout, pointer, z-index, ``@layer``, or
    ``!important`` declarations. Pass ``files`` (the pack's declared file
    keys) to also require every slice ``src`` to be declared.
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

    lines: List[str] = ["html body .ui-root {"]
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
        lines.append(f'html body .ui-root [data-office-slice="{name}"] {{')
        lines.append(
            f'  border-image: url("{src}") '
            f"{cell[0]} {cell[1]} {cell[2]} {cell[3]} {repeat};")
        lines.append(f"  border-image-width: {descriptor['width']};")
        lines.append("}")
    return "\n".join(lines) + "\n"
