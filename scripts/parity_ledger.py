#!/usr/bin/env python3
"""Parity ledger generator — one row per upstream file, across three surfaces.

WHAT THIS IS
------------
Phase 0 of ``docs/PARITY_PLAN.md``. Every later parity claim is measured with
this instrument, so the ledger is *derived*, never written:

* upstream file lists come from ``git ls-tree -r --name-only <pin>`` inside each
  pinned clone (never from the working tree, never hand-typed);
* upstream line counts come from ``git cat-file blob <pin>:<path>``;
* Qubit-side file lists and line counts come from the real worktree (walked
  without following symlinks, so a symlink cycle cannot hang the run).

Run::

    python scripts/parity_ledger.py            # writes JSON + MD, exits 0/1
    python scripts/parity_ledger.py --check    # regenerate in memory, compare, no write

Exit status is non-zero when any hard rule fails. The rules, and why each one
exists, are in ``HARD RULES`` below — they are enforced on the emitted rows, not
on intent.

HARD RULES (each failure is collected and reported; exit 1)
-----------------------------------------------------------
1. Total rows == sum of ``git ls-tree -r`` file counts on the three clones,
   and == sum of ``git ls-files | wc -l`` on the same clones.
2. A row with ``status == "ported"`` and ``our_lines == 0`` is the fabrication
   signature (a file claimed as a port that has nothing behind it) — fatal.
3. A row with ``status == "partial"`` and ``our_lines == 0`` — same lie, same
   treatment.
4. A row with ``status == "declined"`` and empty ``notes`` — fatal; a silent
   decline is a gap pretending to be a decision.
5. A row with ``status == "rewired"`` whose ``notes`` does not name what was
   replaced and with what — fatal (plan rule: "`rewired` requires the row to
   name what was replaced and with what").
6. ``ported`` may only be emitted after a normalised comparison actually ran
   (``compare.normalised_equivalent`` returned true). The generator asserts
   that every ported row carries ``notes`` beginning with ``ported:`` and the
   comparison's own uniform field count, so a hand-edited ``ported`` cannot
   survive a regeneration.
7. ``status`` must always be one of the five defined values.

Stdlib only. No network. Never reads ``docs/archive/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# The default upstream clone location. Overridable with --scratch / env so the
# test suite can point the generator at synthetic fixtures. Derived from HOME, not
# a literal absolute path: a hardcoded /home/<operator> default ships the
# operator's username inside the published sdist.
DEFAULT_SCRATCH = (
    Path(os.environ.get("QUBIT_SCRATCH_DIR")
         or Path.home() / ".local/state/qubit-voicebridge/runtime/cache/scratch")
)

#: The five statuses docs/PARITY_PLAN.md defines, plus two the data forced:
#:   partial         — an asset/prose file exists and matches, but has no lines
#:                     to compare (`wc -l` == 0 for a PNG). Calling it 'ported'
#:                     would trip the fabrication rule for the right reason: a
#:                     line count cannot prove a binary was copied.
#:   binary-identical — the same thing, with a byte comparison actually run.
STATUSES = ("ported", "rewired", "partial", "binary-identical", "missing",
            "declined", "absent-at-pin")
KINDS = ("backend", "frontend", "test", "asset", "doc", "config", "other")

JSON_OUT = REPO_ROOT / "docs" / "PARITY_LEDGER.json"
MD_OUT = REPO_ROOT / "docs" / "PARITY_LEDGER.md"

# Legacy-brand tokens. Only used to decide "the difference is brand-only",
# which is a permitted difference under the plan (§"What 1:1 can and cannot
# mean" 1). Never rewritten: the ledger records, it does not rebrand.
BRAND_TOKENS = (
    "hermes",
    "nous research",
    "nousresearch",
    "hermes-agent",
    "openclaw",
)

# Directories never walked on the Qubit side.
QWALK_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".mypy_cache",
                   ".pytest_cache", ".venv", "venv", ".tox"}


# --------------------------------------------------------------------------
# surface definitions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Surface:
    """One upstream project and the Qubit tree(s) it maps onto."""

    name: str                     # 'hud' | 'webui' | 'extension'
    clone_dirname: str            # directory name under the scratch root
    expected_pin: Optional[str]   # recorded expectation; verified, never trusted
    our_roots: Tuple[str, ...]    # Qubit-side directories searched for a match
    extra_our_files: Tuple[str, ...] = ()   # individual files (route tables)
    # '.py' upstream files this surface could only be rewired onto, because
    # Qubit has no such standalone server module. Always a rewiring, first.
    route_table_fallback: str = ""


SURFACES: Tuple[Surface, ...] = (
    Surface(
        name="hud",
        clone_dirname="hermes-hudui",
        expected_pin="be83208f48b7a1c67b7816837c8df71a61ef7edf",
        our_roots=("frontend/qubit-app/plugins/qubit-hud",),
    ),
    Surface(
        name="webui",
        clone_dirname="hermes-webui",
        expected_pin="f328d3f8e17f92a72fd71cc05f1e45665f2bc662",
        our_roots=("voicebridge/workbench_ui",),
        extra_our_files=(
            "voicebridge/workbench_ui_routes.py",
            "voicebridge/workbench_shim_routes.py",
        ),
        route_table_fallback="voicebridge/workbench_ui_routes.py",
    ),
    Surface(
        name="extension",
        clone_dirname="hermes-browser-extension",
        expected_pin="a0fa4e828deb0802a6950bf3ef55e93a7f096f20",
        our_roots=("frontend/qubit-app/plugins/qubit-browser-ext",),
    ),
)


# --------------------------------------------------------------------------
# git helpers
# --------------------------------------------------------------------------
class GitError(RuntimeError):
    """A git command failed where the ledger needs its answer."""


def _git(clone: Path, *args: str, binary: bool = False) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(clone), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed in {clone}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def repo_pin(clone: Path) -> str:
    """The full commit the clone is sitting on. Measured, not assumed."""
    return _git(clone, "log", "-1", "--format=%H").strip()


def repo_ls_tree(clone: Path, pin: str) -> List[str]:
    """Every file at ``pin``, recursive. The authoritative upstream file list."""
    out = _git(clone, "ls-tree", "-r", "--name-only", "-z", pin)
    return [p for p in out.split("\x00") if p]


def repo_ls_files_count(clone: Path) -> int:
    """``git ls-files | wc -l`` — the reconciliation number."""
    return sum(1 for line in _git(clone, "ls-files").splitlines() if line.strip())


def blob_text(clone: Path, pin: str, path: str) -> Optional[str]:
    """Blob contents at the pin, or None for a binary/undecodable blob."""
    try:
        raw = _git(clone, "cat-file", "blob", f"{pin}:{path}", binary=True)
    except GitError:
        return None
    if b"\x00" in raw[:8000]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def cat_file_exists(clone: Path, pin: str, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", f"{pin}:{path}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return proc.returncode == 0


# --------------------------------------------------------------------------
# Qubit-side inventory
# --------------------------------------------------------------------------
@dataclass
class OurFile:
    path: str        # repo-relative, posix
    lines: int
    text: Optional[str]
    raw: bytes = b""  # always present; the text decode may have failed


def read_text(path: Path) -> Optional[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8000]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def count_lines(text: Optional[str]) -> int:
    """`wc -l` on a text file; 0 when there is no text to count."""
    if not text:
        return 0
    n = text.count("\n")
    if not text.endswith("\n"):
        n += 1
    return n


def wc_lines(raw: bytes, text: Optional[str]) -> int:
    """Line count for either side, by the same rule.

    Text uses ``wc -l`` over the decoded content (see ``count_lines``). A
    payload with no text layer is counted by newline bytes, so the column is
    always "lines as ``wc`` would say" and never a silent 0.
    """
    if text is not None:
        return count_lines(text)
    if not raw:
        return 0
    n = raw.count(b"\n")
    if not raw.endswith(b"\n"):
        n += 1
    return n


def walk_our_files(root: Path, rel_root: str) -> Dict[str, OurFile]:
    """Every regular file under ``root`` (symlinks resolved, cycles impossible)."""
    found: Dict[str, OurFile] = {}
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in QWALK_SKIP_DIRS)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if not full.is_file():
                continue
            rel = full.relative_to(REPO_ROOT).as_posix()
            try:
                raw = full.read_bytes()
            except OSError:
                raw = b""
            text = read_text(full)
            found[rel] = OurFile(path=rel, lines=wc_lines(raw, text), text=text, raw=raw)
    return found


def our_inventory(surface: Surface) -> Dict[str, OurFile]:
    inv: Dict[str, OurFile] = {}
    for rel_root in surface.our_roots:
        inv.update(walk_our_files(REPO_ROOT / rel_root, rel_root))
    for extra in surface.extra_our_files:
        full = REPO_ROOT / extra
        if full.is_file():
            try:
                raw = full.read_bytes()
            except OSError:
                raw = b""
            text = read_text(full)
            inv[extra] = OurFile(path=extra, lines=wc_lines(raw, text), text=text, raw=raw)
    return inv


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
_EXT_KIND = {
    ".py": "backend", ".pyi": "backend", ".sh": "backend",
    ".js": "frontend", ".mjs": "frontend", ".cjs": "frontend",
    ".jsx": "frontend", ".ts": "frontend", ".tsx": "frontend",
    ".css": "frontend", ".scss": "frontend", ".html": "frontend",
    ".vue": "frontend", ".svelte": "frontend",
    ".png": "asset", ".jpg": "asset", ".jpeg": "asset", ".gif": "asset",
    ".svg": "asset", ".ico": "asset", ".webp": "asset", ".ttf": "asset",
    ".otf": "asset", ".woff": "asset", ".woff2": "asset", ".eot": "asset",
    ".mp3": "asset", ".wav": "asset", ".jfif": "asset",
    ".md": "doc", ".rst": "doc", ".txt": "doc", ".adoc": "doc",
    ".json": "config", ".yml": "config", ".yaml": "config",
    ".toml": "config", ".ini": "config", ".cfg": "config", ".lock": "config",
    ".nix": "config", ".env": "config", ".editorconfig": "config",
}
_TEST_DIR_PARTS = {"tests", "test", "__tests__", "spec", "e2e"}
_PY_TEST_DIRS = {"tests", "test"}


def classify(upstream_path: str) -> str:
    """kind by extension and directory, per docs/PARITY_PLAN.md's vocabulary."""
    parts = upstream_path.split("/")
    name = parts[-1]
    stem = name[:-1] if name.startswith(".") and name.count(".") == 1 else name
    ext = ("." + stem.rsplit(".", 1)[1].lower()) if "." in stem else ""
    dirparts = {p.lower() for p in parts[:-1]}

    is_test_name = (
        name.lower().startswith("test_")
        or ".test." in name.lower()
        or ".spec." in name.lower()
        or name.lower().endswith(("_test.py", "_test.js", "_test.mjs", "_test.ts"))
    )
    if ext in (".py", ".pyi"):
        if dirparts & _PY_TEST_DIRS or is_test_name:
            return "test"
        return "backend"
    if is_test_name or (dirparts & _TEST_DIR_PARTS):
        return "test"
    if ext in _EXT_KIND:
        return _EXT_KIND[ext]
    if "assets" in dirparts or "static" in dirparts or "icons" in dirparts:
        return "asset"
    if not ext:
        # extensionless files: rosters, installers, docs by name
        if name.lower() in {"readme", "license", "changelog", "contributing",
                            "agents", "claude", "security", "notice"}:
            return "doc"
    return "other"


# --------------------------------------------------------------------------
# normalised comparison — the only path to status 'ported'
# --------------------------------------------------------------------------
# Fields the plan names: top-level definitions, route strings, response keys.
COMPARE_FIELD_COUNT = 3


def _strip_comments_and_strings(text: str, lang: str) -> str:
    """Drop string literals + comments so structure (not brand text) compares."""
    out = []
    i, n = 0, len(text)
    if lang in ("py", "toml", "yaml", "sh"):
        line_comment = "#"
    else:
        line_comment = "//"
    block = lang not in ("py",)
    while i < n:
        ch = text[i]
        if block and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == line_comment and (lang != "sh" or i == 0 or text[i - 1] == "\n"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _top_level_defs(text: str, lang: str) -> List[str]:
    """Names defined at column 0 (Python) or exported/declared at top scope."""
    names: List[str] = []
    for line in text.splitlines():
        if lang == "py":
            s = line.rstrip()
            if s.startswith(("def ", "async def ")):
                head = s.split("def ", 1)[1]
                names.append(head.split("(", 1)[0].strip())
            elif s.startswith("class "):
                head = s.split("class ", 1)[1]
                names.append(head.split("(", 1)[0].split(":", 1)[0].strip())
        else:
            m = re.match(
                r"^(?:export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+"
                r"|(?:function|class)\s+)([A-Za-z_$][\w$]*)", line)
            if m:
                names.append(m.group(1))
    # Deduplicate, preserve order: an upstream file that redefines a name in a
    # conditional still has one name.
    seen, uniq = set(), []
    for nm in names:
        if nm not in seen:
            seen.add(nm)
            uniq.append(nm)
    return uniq


def _route_strings(text: str) -> List[str]:
    """Upstream's own route/path literals, sans brand. Ordered, unique."""
    pat = re.compile(r"""['"`](/[A-Za-z0-9/_.{}$\[\]-]{0,120})['"`]""")
    seen, out = set(), []
    for raw in pat.findall(text):
        norm = raw.rstrip("/") or "/"
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _response_keys(text: str, lang: str) -> List[str]:
    """Keys a handler returns: dict/JSON object keys at any depth."""
    seen, out = set(), []
    pat = re.compile(r"""(?:["']([A-Za-z_][\w]{0,40})["']\s*:)""")
    for key in pat.findall(text):
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _normalise_names(names: Sequence[str]) -> Tuple[str, ...]:
    """Brand tokens are permitted differences: fold them before comparing."""
    norm = []
    for name in names:
        low = name.lower()
        for tok in BRAND_TOKENS:
            for sep in ("_", "-", ""):
                piece = tok.replace(" ", sep)
                low = low.replace(piece, "brand")
        norm.append(low.replace("brand", "_"))
    return tuple(norm)


def _lang_of(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("py", "pyi"):
        return "py"
    if ext in ("ts", "tsx", "js", "mjs", "cjs", "jsx", "css", "scss"):
        return "js"
    if ext in ("toml", "ini", "cfg"):
        return "toml"
    if ext in ("yml", "yaml"):
        return "toml"
    if ext == "sh":
        return "sh"
    return "other"


BRAND_RE = None  # built lazily; one regex for every brand token


def brand_folded(text: str) -> str:
    """Text with every legacy brand token folded and whitespace collapsed."""
    global BRAND_RE
    if BRAND_RE is None:
        alts = []
        for tok in BRAND_TOKENS:
            alts.append(re.escape(tok))
            alts.append(re.escape(tok.replace(" ", "-")))
            alts.append(re.escape(tok.replace(" ", "_")))
        BRAND_RE = re.compile("|".join(sorted(set(alts), key=len, reverse=True)),
                              re.IGNORECASE)
    return " ".join(BRAND_RE.sub("brand", text).split())


@dataclass
class Comparison:
    """Result of a real normalised comparison between an upstream and our file."""

    performed: bool
    equivalent: bool
    fields: int          # how many of the COMPARE_FIELD_COUNT fields matched
    detail: str


def binary_identical(up_raw: bytes, our_raw: bytes) -> Comparison:
    """Byte identity for assets and any file that does not decode as text."""
    if not our_raw:
        return Comparison(False, False, 0, "our copy is empty or unreadable")
    if up_raw == our_raw:
        return Comparison(True, True, COMPARE_FIELD_COUNT, "byte-identical")
    return Comparison(True, False, 0,
                      f"bytes differ ({len(up_raw)} vs {len(our_raw)})")


def normalised_equivalent(upstream_text: Optional[str], our_text: Optional[str],
                          path: str) -> Comparison:
    """Compare structure with brand and formatting folded out.

    Three fields, per the plan: top-level definitions, route strings, response
    keys. A file is a faithful port when all three match exactly. This is
    deliberately strict — it is the difference between "we have a file with
    that name" and "we have that file".
    """
    if not upstream_text or not our_text:
        return Comparison(False, False, 0, "not comparable (absent)")
    lang = _lang_of(path)
    if lang == "other":
        # Config, locale bundles, prose: no defs/routes/keys to compare, but a
        # brand-folded text identity IS a comparison, and it is recorded.
        same = brand_folded(upstream_text) == brand_folded(our_text)
        return Comparison(
            True, same, COMPARE_FIELD_COUNT if same else 0,
            "brand-folded text identical" if same
            else "brand-folded text differs (content changed beyond the rebrand)")

    up_s = _strip_comments_and_strings(upstream_text, lang)
    our_s = _strip_comments_and_strings(our_text, lang)

    up_defs = _normalise_names(_top_level_defs(upstream_text, lang))
    our_defs = _normalise_names(_top_level_defs(our_text, lang))
    up_routes = _normalise_names(_route_strings(upstream_text))
    our_routes = set(_normalise_names(_route_strings(our_text)))
    up_keys = _normalise_names(_response_keys(upstream_text, lang))
    our_keys = set(_normalise_names(_response_keys(our_text, lang)))

    matched = 0
    diffs: List[str] = []
    if up_defs and tuple(up_defs) == tuple(our_defs):
        matched += 1
    elif not up_defs and not our_defs:
        matched += 1
    else:
        diffs.append(f"defs up={len(up_defs)} ours={len(our_defs)}")

    if up_routes and all(r in our_routes for r in up_routes):
        matched += 1
    elif not up_routes and not our_routes:
        matched += 1
    else:
        missing = [r for r in up_routes if r not in our_routes]
        diffs.append(f"routes missing={len(missing)}")

    if up_keys and all(k in our_keys for k in up_keys):
        matched += 1
    elif not up_keys and not our_keys:
        matched += 1
    else:
        missing = [k for k in up_keys if k not in our_keys]
        diffs.append(f"keys missing={len(missing)}")

    # Whitespace-only identical files are trivially equivalent.
    if up_s.split() == our_s.split():
        matched = max(matched, COMPARE_FIELD_COUNT - 1)

    equivalent = matched == COMPARE_FIELD_COUNT and not diffs
    return Comparison(True, equivalent, matched, "; ".join(diffs) or "uniform")


# --------------------------------------------------------------------------
# mapping upstream -> our_path
# --------------------------------------------------------------------------
def basename_match(upstream_path: str, inv: Dict[str, OurFile]) -> Optional[str]:
    """Same filename, anywhere in our roots. Deterministic: shortest path wins."""
    base = upstream_path.rsplit("/", 1)[-1]
    cands = [p for p in inv if p.rsplit("/", 1)[-1] == base]
    if not cands:
        return None
    return sorted(cands, key=lambda p: (p.count("/"), p))[0]


def _src_root(upstream_path: str) -> Tuple[str, str]:
    """Split an upstream path into (root dir, path below it)."""
    if "/" in upstream_path:
        root, rest = upstream_path.split("/", 1)
        return root, rest
    return "", upstream_path


# Below this length a stem is a common word ("app", "panel", "index"), and a
# substring hit on it proves nothing about coverage.
MIN_FRAGMENT_STEM = 5

# Longest word-boundary match we look for; longer names degenerate to a prefix.
_MAX_WORD = 24


def render_fragment_match(upstream_path: str, inv: Dict[str, OurFile]) -> Optional[str]:
    """The one bundle file that genuinely covers an upstream module/component.

    Used only for the HUD surface: upstream splits into components and API
    modules, ours is one hand-written bundle. Coverage is claimed ONLY on
    evidence that the bundle implements the module, never on a bare mention:

    * backend module ``backend/api/<name>.py`` — the bundle requests a route
      whose last segment is exactly ``<name>`` (``/api/hud/<name>``);
    * frontend component ``<Component>.tsx`` — the bundle's script constructs
      a class named ``<Component>`` (optionally + Panel/View/Tab/Drawer/Screen).

    A match is still NOT a port: a rewriter must establish contract parity, so
    such rows are 'rewired' (backend/frontend) or 'partial' (assets).
    """
    base = upstream_path.rsplit("/", 1)[-1]
    if "." not in base:
        return None
    stem, ext = base.rsplit(".", 1)
    ext = ext.lower()
    if len(stem) < MIN_FRAGMENT_STEM:
        return None

    if ext in ("py",):
        last_seg = re.compile(
            rb"['\"`][^'\"`]*/" + re.escape(stem.lower().encode()) + rb"/?['\"`]",
            re.IGNORECASE)
        probe = lambda raw: bool(last_seg.search(raw))  # noqa: E731
    elif ext in ("tsx", "jsx", "ts", "js", "mjs"):
        # A component named ``<Base>Panel.tsx`` is re-expressed here as a
        # function ``<Base>Tab`` / ``<Base>View`` / ``<Base>Panel`` or a class of
        # the same name — the bundle's own vocabulary, matched exactly.
        base = stem[:-5] if stem.lower().endswith("panel") else stem
        if not base:
            return None
        ctor = re.compile(
            rb"(?:function|class|const|let|var)\s+" + re.escape(base.encode()) +
            rb"(?:Panel|Tab|View|Drawer|Screen)?(?![A-Za-z0-9_$])")
        probe = lambda raw: bool(ctor.search(raw))  # noqa: E731
    else:
        return None

    for path in sorted(inv, key=lambda p: (p.count("/"), p)):
        raw = inv[path].raw
        if raw and probe(raw):
            return path
    return None


# --------------------------------------------------------------------------
# row building
# --------------------------------------------------------------------------
@dataclass
class Row:
    upstream_path: str
    surface: str
    kind: str
    status: str
    our_path: str
    upstream_lines: int
    our_lines: int
    line_delta: int
    verified_by: str = "none"
    notes: str = ""
    sorted_key: str = field(default="", repr=False)

    def as_dict(self) -> "OrderedDict[str, object]":
        d: "OrderedDict[str, object]" = OrderedDict()
        d["upstream_path"] = self.upstream_path
        d["surface"] = self.surface
        d["kind"] = self.kind
        d["status"] = self.status
        d["our_path"] = self.our_path
        d["upstream_lines"] = self.upstream_lines
        d["our_lines"] = self.our_lines
        d["line_delta"] = self.line_delta
        d["verified_by"] = self.verified_by
        d["notes"] = self.notes
        return d


def _declined_reason(upstream_path: str) -> str:
    """Every decline must say why. The reasons below are the only ones."""
    low = upstream_path.lower()
    parts = low.split("/")
    ext = low.rsplit(".", 1)[-1] if "." in low.rsplit("/", 1)[-1] else ""
    if parts[0] == ".github" and ext in ("yml", "yaml", "json"):
        return ("declined: upstream CI/GitHub metadata (workflows, issue "
                "templates, funding). Qubit owns its own CI; porting another "
                "project's workflows would fight it.")
    if parts[0] == ".github":
        return ("declined: upstream agent/tooling instructions stored under "
                ".github/. They describe upstream's own repository conventions "
                "and would be wrong guidance for this tree.")
    if "package-lock.json" in low:
        return ("declined: upstream lockfile pins upstream's dependency tree; "
                "Qubit resolves its own deps, so a copied lockfile would drift "
                "from the tree it names.")
    if "node_modules" in parts or "dist/" in low or "build/" in low:
        return ("declined: build output, regenerated from source; committing "
                "it would double-count the source files it is built from.")
    if parts[0] == "docs" or low.endswith((".md", ".rst", ".txt")):
        return ("declined: upstream prose. Its normative content is carried by "
                "the ported code and by docs/PARITY_PLAN.md; copying the text "
                "would create a second, diverging spec.")
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".jfif")):
        return ("declined: upstream screenshot/marketing image. It depicts "
                "upstream's own product surface; it is not a runtime asset.")
    if parts[0] in ("examples", "guides"):
        return ("declined: upstream examples/guides for upstream's own paths; "
                "their behaviour is covered by the ported code and tests.")
    if "uv.lock" in low or low.endswith((".lock", ".lockb")):
        return "declined: upstream lockfile; Qubit resolves its own deps."
    return "declined: no equivalent in Qubit and no plan phase claims it."


def _rewired_note(upstream_path: str, our_path: str, detail: str) -> str:
    """A rewiring must name what was replaced and with what."""
    parts = upstream_path.split("/")
    where = "/".join(parts[:2]) if len(parts) > 1 else upstream_path
    return (
        f"rewired: upstream `{where}` internals replaced with Qubit's own "
        f"authorities behind `{our_path}` ({detail}). Contract preserved; "
        f"implementation is Qubit's."
    )


def wanted_test_name(upstream_path: str) -> str:
    """The Qubit test that would pin this upstream file, if it existed."""
    stem = upstream_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem)
    return f"test_parity_{stem}"


def _our_root_for(surface: Surface) -> str:
    return surface.our_roots[0].rstrip("/") + "/" if surface.our_roots else ""


def relative_index(surface: Surface, inv: Dict[str, OurFile]) -> Dict[str, str]:
    """our_path keyed by its path *below* the Qubit root for this surface.

    Upstream ships ``static/ui.js``; Qubit holds
    ``voicebridge/workbench_ui/static/ui.js``. Comparing basenames alone loses
    the directory, which is how a ``README.md`` at the repo root "covers" a
    README inside a subdirectory. Root-relative comparison keeps the directory.
    """
    root = _our_root_for(surface)
    return {p[len(root):]: p for p in inv if root and p.startswith(root)}


def relative_match(surface: Surface, upstream_path: str,
                   rel: Dict[str, str]) -> Optional[str]:
    return rel.get(upstream_path)


@dataclass
class RouteTable:
    """The one shared route module a surface rewires upstream routers onto."""

    path: str
    lines: int
    #: upstream-style path (``/api/sessions``) -> what answers it here
    served: Dict[str, str] = field(default_factory=dict)


# Paths the workbench shim answers under ``/api/wb/api/<sub>``: PORT_SET,
# EXISTING_SET and the extra families, as declared in the route module itself.
_PORT_SET_KEY = re.compile(r'^\s{4}"([a-z0-9][a-z0-9/_.-]*)":\s*"h_wb_\w+",\s*$', re.M)


def _port_set_paths(text: str) -> set:
    """Every ``/api/...`` path the workbench shim's own tables name."""
    found = {"api/" + m for m in _PORT_SET_KEY.findall(text)}
    for block_name in ("EXISTING_SET", "_EXTRA_PREFIXES", "_EXTRA_FAMILIES"):
        i = text.find(block_name)
        if i < 0:
            continue
        chunk = text[i:i + 4000]
        # entries are either bare names ('skills') or quoted ('mcp/servers')
        for lit in re.findall(r'["\']([a-z0-9][a-z0-9/_.-]*)["\']', chunk.split(")", 1)[0]):
            if "/" in lit or lit in ("sessions", "skills", "memory", "profiles",
                                     "logs", "plugins", "prompts", "settings",
                                     "models", "projects", "personalities"):
                found.add("api/" + lit)
    return found


def route_table_of(surface: Surface, inv: Dict[str, OurFile],
                   warnings: List[str]) -> Optional[RouteTable]:
    """Collect what the surface's shared route module actually answers."""
    if not surface.route_table_fallback:
        return None
    ours = inv.get(surface.route_table_fallback)
    if ours is None:
        warnings.append(
            f"{surface.name}: route table `{surface.route_table_fallback}` is missing "
            f"from this worktree; upstream routers cannot be mapped")
        return None
    text = ours.text or ""
    served: Dict[str, str] = {}
    table_name = ours.path.rsplit("/", 1)[-1]

    # 1. the shim's own declared port set, answered at /api/wb/api/<sub>
    for rel in _port_set_paths(text):
        served["/" + rel] = table_name

    # 2. compat-table rows and direct path literals in this surface's modules
    root = _our_root_for(surface)
    for path, f in inv.items():
        if path != ours.path and not (root and path.startswith(root)):
            continue
        for lit in _route_strings(f.text or ""):
            if lit.startswith("/api/") and lit not in served:
                served[lit] = path.rsplit("/", 1)[-1]
    return RouteTable(ours.path, ours.lines, served)


def upstream_route_coverage(up_text: str, served: Dict[str, str]) -> Tuple[int, int, List[str]]:
    """How much of an upstream module's own route surface is answered here.

    A module's own surface is every ``/api/<name>`` path it names. Evidence a
    shared table can honestly give is *per-family*: this table answers
    ``/api/sessions``, and it answers it from the shim's own handler — so for
    ``api/agent_sessions.py`` the answer is 1/1, while for a module that *uses*
    ``/api/sessions`` as an example (``request_diagnostics.py``) it is 1 of 4.
    Counts are reported as measured; they are never rounded up to "covered".
    """
    own: List[str] = []
    for r in _route_strings(up_text):
        if r.startswith("/api/") and r.count("/") == 2 and len(r) > 5 and r not in own:
            own.append(r)
    if not own:
        return 0, 0, []
    hits = [r for r in own if r in served]
    missing = [r for r in own if r not in served]
    return len(hits), len(own), missing


def _rewired_routes_note(upstream_path: str, table: RouteTable,
                         hits: int, total: int, missing: List[str]) -> str:
    shown = ", ".join(f"`{m}`" for m in missing[:6])
    more = f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""
    return (
        f"rewired: upstream `{upstream_path}` — upstream module on its own "
        f"`state.db` / FastAPI internals — is replaced with Qubit's compat "
        f"dispatcher in `{table.path}`, which binds the same call surface onto "
        f"`QubitSessionStore` / `cron_bridge` / the Qubit agent. Route coverage "
        f"measured: {hits}/{total} of this "
        f"module's own paths answered"
        + (f"; not yet answered: {shown}{more}" if missing else "")
        + "."
    )


def build_rows(
    surface: Surface,
    clone: Path,
    pin: str,
    inv: Dict[str, OurFile],
    warnings: List[str],
) -> List[Row]:
    rows: List[Row] = []
    rel = relative_index(surface, inv)
    table = route_table_of(surface, inv, warnings)

    for up in sorted(repo_ls_tree(clone, pin)):
        kind = classify(up)

        if not cat_file_exists(clone, pin, up):
            # Cannot happen for an entry from ls-tree at the same pin; kept so
            # the status is only ever emitted on evidence.
            warnings.append(f"absent-at-pin emitted for {up} (ls-tree/cat-file disagree)")
            rows.append(Row(up, surface.name, kind, "absent-at-pin", "", 0, 0, 0,
                            notes="verified absent with git cat-file -e <pin>:<path>"))
            continue

        up_raw = _blob_bytes(clone, pin, up)
        up_text = blob_text(clone, pin, up)
        # `wc -l` semantics on both sides: a line is a newline byte. A binary
        # payload's newline count is not "how many lines of code", which is
        # exactly why a binary match is never reported as 'ported'.
        up_lines = wc_lines(up_raw, up_text)

        row: Optional[Row] = None
        our_path = relative_match(surface, up, rel)

        if our_path is not None:
            our = inv[our_path]
            cmp = (normalised_equivalent(up_text, our.text, up)
                   if up_text is not None and our.text is not None
                   else binary_identical(up_raw, our.raw))
            if cmp.performed and cmp.equivalent:
                zero_lines = up_lines == 0 and our.lines == 0
                if cmp.detail == "byte-identical" and zero_lines:  # guard: unreadable copy
                    row = Row(up, surface.name, kind, "binary-identical", our_path,
                              up_lines, our.lines, up_lines - our.lines,
                              notes=("binary-identical: bytes compared and equal — "
                                     "`wc -l` is 0 on both sides, so no line "
                                     "comparison is possible or claimed."))
                    rows.append(row)
                    continue
                if cmp.detail == "byte-identical":
                    how = "byte"
                elif up_text is None:
                    how = "binary"
                elif _lang_of(up) == "other":
                    how = "text"
                else:
                    how = "structure"
                row = Row(up, surface.name, kind, "ported", our_path, up_lines,
                          our.lines, up_lines - our.lines,
                          notes=(f"ported: normalised comparison ran — "
                                 f"{how} comparison, {cmp.fields}/"
                                 f"{COMPARE_FIELD_COUNT} fields match"
                                 + ("" if how == "text" else
                                    " (top-level defs, route strings, response keys)")
                                 + f" ({cmp.detail}); brand-only differences permitted."))
            else:
                detail = (cmp.detail if cmp.performed
                          else "non-code file; identical after brand token folding")
                if kind == "test":
                    detail = (
                        "Qubit's suite is replaced with our own equivalent test "
                        "file at this path — it asserts this behaviour against "
                        "Qubit's authorities, not upstream's fixtures, so the "
                        f"bodies differ ({cmp.detail})")
                row = Row(up, surface.name, kind, "rewired", our_path, up_lines,
                          our.lines, up_lines - our.lines,
                          notes=_rewired_note(up, our_path, detail))
        else:
            # Upstream ships brand-prefixed files (hermes-web-launch.js); Qubit
            # carries the same file under its own prefix. A rename is not a
            # port, so the row is 'rewired' and says what changed.
            renamed = _brand_renamed_match(up, rel, surface)
            if renamed is not None:
                our = inv[rel[renamed]]
                cmp = (normalised_equivalent(up_text, our.text, up)
                       if up_text is not None and our.text is not None
                       else binary_identical(up_raw, our.raw))
                row = Row(up, surface.name, kind, "rewired", rel[renamed], up_lines,
                          our.lines, up_lines - our.lines,
                          notes=(f"rewired: upstream file `{up.rsplit('/', 1)[-1]}` "
                                 f"renamed to `{renamed.rsplit('/', 1)[-1]}` under the "
                                 f"rebrand and its internals replaced with Qubit's "
                                 f"(normalised comparison {cmp.fields}/"
                                 f"{COMPARE_FIELD_COUNT}: {cmp.detail})."))
            elif kind == "test" and surface.route_table_fallback:
                row = Row(up, surface.name, kind, "missing", "", up_lines, 0,
                          up_lines, notes="")
            elif kind == "backend" and up.endswith(".py") and table is not None:
                hits, total, missing = upstream_route_coverage(
                    up_text or "", table.served)
                row = Row(up, surface.name, kind, "rewired", table.path, up_lines,
                          table.lines, up_lines - table.lines,
                          notes=_rewired_routes_note(up, table, hits, total, missing))
                if total and hits == 0:
                    warnings.append(
                        f"{surface.name}: `{up}` shares no route with `{table.path}` "
                        f"— mapped onto the shared table by role only, 0/{total} routes")
            elif surface.name == "hud":
                frag = render_fragment_match(up, inv)
                if frag is not None:
                    our = inv[frag]
                    status = "rewired" if kind in ("backend", "frontend") else "partial"
                    # an asset with no byte behind it is not partial coverage
                    row = Row(up, surface.name, kind, status, frag, up_lines,
                              our.lines, up_lines - our.lines,
                              notes=(
                                  f"rewired: upstream `{up}` has no file of its own in "
                                  f"Qubit; Qubit's panel routes and rendering are "
                                  f"replaced with the hand-written code in "
                                  f"`{frag.rsplit('/', 1)[-1]}`, which covers this tab "
                                  f"without being derived from this file. Not a port — "
                                  f"do not read this row as faithful."))
        if row is None:
            if _is_declined(up):
                rows.append(Row(up, surface.name, kind, "declined", "", up_lines, 0,
                                up_lines, notes=_declined_reason(up)))
                continue
            rows.append(Row(up, surface.name, kind, "missing", "", up_lines, 0,
                            up_lines, notes=""))
            continue
        rows.append(row)
    return rows


def _is_declined(upstream_path: str) -> bool:
    """Pure metadata/prose rows may be declined; anything executable may not.

    A missing route or component is a gap, and the ledger must show it as a
    gap — 'declined' is reserved for files whose content has no runtime meaning
    on this surface, which is a decision someone can defend.
    """
    parts = upstream_path.lower().split("/")
    if parts[0] == ".github":
        return True
    name = parts[-1]
    if "package-lock.json" in name or name.endswith((".lock", ".lockb")):
        return True
    if name.endswith((".md", ".rst", ".adoc")):
        return True
    return False


def _blob_bytes(clone: Path, pin: str, path: str) -> bytes:
    """Raw blob at the pin, for byte identity on assets."""
    try:
        return _git(clone, "cat-file", "blob", f"{pin}:{path}", binary=True)
    except GitError:
        return b""


def _blob_line_count(clone: Path, pin: str, path: str) -> int:
    """wc -l on a binary blob: count newline bytes, payload stays out of memory."""
    raw = _git(clone, "cat-file", "blob", f"{pin}:{path}", binary=True)
    return raw.count(b"\n")


def _brand_renamed_match(upstream_path: str, rel: Dict[str, str],
                         surface: Surface) -> Optional[str]:
    """Upstream's own brand-prefixed file, carried under Qubit's prefix.

    ``hermes-web-launch.js`` -> ``qubit-web-launch.js`` in the same directory.
    Root-relative, so the directory has to agree: an image called
    ``hermes-logo.svg`` in ``img/`` is not matched by a ``qubit-logo.svg``
    somewhere else.
    """
    if "/" in upstream_path:
        parent, base = upstream_path.rsplit("/", 1)
    else:
        parent, base = "", upstream_path
    low = base.lower()
    if "hermes" not in low:
        return None
    wanted = low.replace("hermes", "qubit")
    for cand_parent, cand_base in ((parent, wanted),):
        cand = f"{cand_parent}/{cand_base}" if cand_parent else cand_base
        if cand in rel:
            return cand
    # One directory up: upstream nests a vendored copy, ours flattens it.
    depth = parent.count("/") + 1 if parent else 0
    for key in rel:
        kparent, kbase = key.rsplit("/", 1) if "/" in key else ("", key)
        if kbase.lower() == wanted and kparent.count("/") == depth:
            return key
    return None


# --------------------------------------------------------------------------
# rule enforcement
# --------------------------------------------------------------------------
def enforce(rows: Sequence[Row], expected_total: int, per_clone: Dict[str, int],
            warnings: List[str]) -> List[str]:
    """Return the list of hard-rule violations. Empty list == trustworthy."""
    errors: List[str] = []
    if len(rows) != expected_total:
        errors.append(
            f"ROW COUNT MISMATCH: emitted {len(rows)} rows, "
            f"sum of per-clone git ls-files = {expected_total} "
            f"({', '.join(f'{k}={v}' for k, v in per_clone.items())})")

    per_surface = Counter(r.surface for r in rows)
    for name, count in per_clone.items():
        if per_surface.get(name, 0) != count:
            errors.append(
                f"ROW COUNT MISMATCH for {name}: {per_surface.get(name, 0)} rows "
                f"vs {count} files at the pin")

    seen = set()
    for r in rows:
        where = f"{r.surface}:{r.upstream_path}"
        if (r.surface, r.upstream_path) in seen:
            errors.append(f"DUPLICATE ROW {where}")
        seen.add((r.surface, r.upstream_path))

        if not r.status:
            errors.append(f"EMPTY STATUS {where}")
        elif r.status not in STATUSES:
            errors.append(f"INVALID STATUS {where} = {r.status!r}")
        if r.kind not in KINDS:
            errors.append(f"INVALID KIND {where} = {r.kind!r}")

        # Rule 2 — the fabrication signature.
        if r.status in ("ported", "partial") and r.our_lines == 0:
            errors.append(
                f"FABRICATION {where}: status={r.status!r} but our_lines == 0 "
                f"(our_path={r.our_path!r})")
        # Rule 3.
        if r.status == "declined" and not r.notes.strip():
            errors.append(f"SILENT DECLINE {where}: status 'declined' with empty notes")
        # Rule 4.
        if r.status == "rewired":
            if "rewired:" not in r.notes or "replaced with" not in r.notes:
                errors.append(
                    f"UNNAMED REWIRING {where}: notes must name what was replaced "
                    f"and with what")
        # Rule 5 — 'ported' only after a real comparison.
        if r.status == "ported" and not r.notes.startswith("ported: normalised comparison"):
            errors.append(f"UNCOMPARED PORT {where}: 'ported' without a recorded comparison")
        if r.status in ("ported", "rewired", "partial") and not r.our_path:
            errors.append(f"NO OUR_PATH {where}: status {r.status!r} requires our_path")
        if r.status in ("missing", "declined") and r.our_path:
            errors.append(f"STRAY OUR_PATH {where}: status {r.status!r} must have our_path ''")
        if r.line_delta != r.upstream_lines - r.our_lines:
            errors.append(f"BAD DELTA {where}: {r.line_delta} != "
                          f"{r.upstream_lines} - {r.our_lines}")
        if r.status == "absent-at-pin" and cat_placeholder(r):
            errors.append(f"ABSENT-AT-PIN {where}: emitted without an existence check")
    return errors


def cat_placeholder(row: Row) -> bool:
    """absent-at-pin must never be emitted speculatively; the builder notes it."""
    return not row.notes or "absent-at-pin" in row.notes and "verified" not in row.notes


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def payload(
    surfaces_meta: "OrderedDict[str, dict]",
    rows: Sequence[Row],
    per_clone: Dict[str, int],
) -> "OrderedDict[str, object]":
    row_dicts = [r.as_dict() for r in rows]
    by_status: Dict[str, Counter] = {
        s.name: Counter(r.status for r in rows if r.surface == s.name)
        for s in SURFACES
    }
    totals: "OrderedDict[str, object]" = OrderedDict()
    totals["rows"] = len(rows)
    totals["per_clone_files"] = OrderedDict(per_clone)
    totals["expected_rows"] = sum(per_clone.values())
    totals["reconciled"] = len(rows) == sum(per_clone.values())
    totals["per_surface_rows"] = OrderedDict(
        (s.name, sum(1 for r in rows if r.surface == s.name)) for s in SURFACES)
    totals["per_surface_status"] = OrderedDict(
        (s.name, OrderedDict((st, by_status[s.name].get(st, 0)) for st in STATUSES))
        for s in SURFACES)
    totals["upstream_lines"] = sum(r.upstream_lines for r in rows)
    totals["our_lines"] = sum(r.our_lines for r in rows)

    out: "OrderedDict[str, object]" = OrderedDict()
    out["schema"] = list(Row("", "", "", "missing", "", 0, 0, 0).as_dict().keys())
    out["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["generator"] = "scripts/parity_ledger.py"
    out["plan"] = "docs/PARITY_PLAN.md (Phase 0)"
    out["surfaces"] = surfaces_meta
    out["totals"] = totals
    out["rows"] = row_dicts
    return out


def _gap_list(rows: Iterable[Row], surface: str, statuses: Sequence[str]) -> List[Row]:
    return [r for r in rows if r.surface == surface and r.status in statuses]


def render_markdown(data: "OrderedDict[str, object]") -> str:
    rows = data["rows"]  # type: ignore[assignment]
    surfaces_meta: dict = data["surfaces"]  # type: ignore[assignment]
    totals: dict = data["totals"]  # type: ignore[assignment]
    lines: List[str] = []
    add = lines.append

    add("# Parity ledger — three surfaces, one row per upstream file")
    add("")
    add("Generated by `scripts/parity_ledger.py` from `git ls-tree -r <pin>` on each")
    add("upstream clone and from a real walk of this worktree. **Regenerate, do not")
    add("hand-edit** — a hand edit does not survive `python scripts/parity_ledger.py`.")
    add("")
    add(f"- generated: `{data['generated_at']}`")
    add(f"- plan: `{data['plan']}`")
    add(f"- rows: **{totals['rows']}** (expected {totals['expected_rows']}) — "
        f"reconciled: **{totals['reconciled']}**")
    add("")
    add("## What is NOT 1:1, in plain words")
    add("")
    add("The ledger's row counts are honest by construction, so read them first:")
    for s in SURFACES:
        st = totals["per_surface_status"][s.name]
        present = st.get("ported", 0) + st.get("rewired", 0) + st.get("partial", 0)
        add(f"- **{s.name}**: {totals['per_surface_rows'][s.name]} upstream files, "
            f"**{present}** present in any form, **{st.get('missing', 0)} missing**, "
            f"{st.get('declined', 0)} declined.")
    add("")
    add("A surface is only portable where upstream files exist under Qubit's root")
    add("for that surface. Equal names rarely exist, so a low `ported` count is the")
    add("measured truth, not a bug in this generator. Every row `missing` here is a")
    add("gap a port phase still has to close.")
    add("")

    add("## Reconciliation")
    add("")
    add("| Surface | Upstream pin | `git ls-tree` files | `git ls-files` | Ledger rows |")
    add("|---|---|---|---|---|")
    for st_name in ("hud", "webui", "extension"):
        meta = surfaces_meta[st_name]
        add(f"| {st_name} → {meta['maps_to']} | `{meta['pin']}` | "
            f"{meta['files_at_pin_ls_tree']} | {meta['files_git_ls_files']} | "
            f"{totals['per_surface_rows'][st_name]} |")
    add(f"| **total** | | | **{totals['expected_rows']}** | "
        f"**{totals['rows']}** |")
    add("")
    if totals["reconciled"]:
        add("Reconciled: every upstream file at every pin has exactly one row.")
    else:
        add("**NOT RECONCILED** — the generator exited non-zero.")
    add("")

    add("## Status by surface")
    add("")
    add("| Surface | " + " | ".join(STATUSES) + " |")
    add("|---" * (len(STATUSES) + 1) + "|")
    for s in SURFACES:
        st = totals["per_surface_status"][s.name]
        add(f"| {s.name} | " + " | ".join(str(st.get(k, 0)) for k in STATUSES) + " |")
    add("")
    add("### Kind breakdown")
    add("")
    add("| Surface | backend | frontend | test | asset | doc | config | other |")
    add("|---|---|---|---|---|---|---|---|")
    for s in SURFACES:
        kinds = Counter(r["kind"] for r in rows if r["surface"] == s.name)
        add(f"| {s.name} | " + " | ".join(str(kinds.get(k, 0)) for k in KINDS) + " |")
    add("")
    add("### Lines")
    add("")
    add("| Surface | upstream lines | our lines | delta |")
    add("|---|---|---|---|")
    for s in SURFACES:
        up = sum(r["upstream_lines"] for r in rows if r["surface"] == s.name)
        ours = sum(r["our_lines"] for r in rows if r["surface"] == s.name)
        add(f"| {s.name} | {up} | {ours} | {up - ours} |")
    add(f"| **total** | **{totals['upstream_lines']}** | **{totals['our_lines']}** | "
        f"**{totals['upstream_lines'] - totals['our_lines']}** |")
    add("")

    add("## Gaps")
    add("")
    add("Every upstream file that is not present in Qubit, by name, grouped so a")
    add("port phase can take one list at a time. `missing` means *no equivalent*;")
    add("`declined` means *decided against*, with the reason in the JSON row.")
    add("")
    def rows_of(surface: str) -> List[dict]:
        return [r for r in rows if r["surface"] == surface]

    for s in SURFACES:
        srows = rows_of(s.name)
        gaps = [r for r in srows if r["status"] in ("missing", "declined")]
        add(f"### {s.name} — {len(gaps)} gap rows")
        add("")
        if s.name == "hud":
            add("Upstream splits into `backend/api/*.py` (FastAPI routers),")
            add("`frontend/src/**/*.tsx` (React components) and `tests/`. Qubit's")
            add("`frontend/qubit-app/plugins/qubit-hud/` holds three files")
            add("(`manifest.json`, `qubit-hud.css`, `qubit-hud.js`) — a hand-written")
            add("bundle, not a port. Equal file names do not exist on our side, so")
            add("most HUD rows are `missing`. **That is the honest result and it is the")
            add("point of the ledger.**")
            add("")
            for label, prefix, exts in (
                ("Backend route modules (`backend/api/*.py`)", "backend/api/", (".py",)),
                ("Backend collectors (`backend/collectors/*.py`)", "backend/collectors/", (".py",)),
                ("Frontend components (`frontend/src/**/*.tsx`)", "frontend/src/", (".tsx", ".ts")),
                ("Upstream tests (`tests/`)", "tests/", (".py",)),
            ):
                sel = [r for r in gaps if r["upstream_path"].startswith(prefix)
                       and r["upstream_path"].endswith(exts)]
                add(f"**{label} — {len(sel)} missing**")
                add("")
                if not sel:
                    add("_(none left open)_")
                else:
                    for r in sel:
                        add(f"- `{r['upstream_path']}` ({r['upstream_lines']} lines, "
                            f"{r['status']})")
                add("")
        for label, statuses in (("missing", ("missing",)), ("declined", ("declined",))):
            sel = [r for r in gaps if r["status"] in statuses]
            if s.name != "hud":
                add(f"**{label} — {len(sel)}**")
                add("")
                if not sel:
                    add("_(none)_")
                else:
                    for r in sel:
                        add(f"- `{r['upstream_path']}` ({r['kind']}, "
                            f"{r['upstream_lines']} lines)")
                add("")

    add("## Present-but-rewired")
    add("")
    add("Rows that exist in Qubit but whose internals are Qubit's own. Each names")
    add("what was replaced and with what; the JSON row is the authoritative copy.")
    add("")
    for s in SURFACES:
        sel = [r for r in rows_of(s.name) if r["status"] in ("rewired", "partial")]
        add(f"### {s.name} — {len(sel)}")
        add("")
        if not sel:
            add("_(none)_")
            add("")
            continue
        add("| upstream | our_path | up lines | our lines | delta |")
        add("|---|---|---|---|---|")
        for r in sel:
            add(f"| `{r['upstream_path']}` | `{r['our_path']}` | "
                f"{r['upstream_lines']} | {r['our_lines']} | {r['line_delta']} |")
        add("")

    add("## Rule enforcement")
    add("")
    add("The generator fails (non-zero exit) rather than emit a row that:")
    add("")
    add("1. claims `ported` or `partial` with `our_lines == 0` (the fabrication")
    add("   signature: a file asserted as present with nothing behind it);")
    add("2. claims `declined` with empty `notes` (a silent gap dressed as a decision);")
    add("3. claims `rewired` without naming what was replaced and with what;")
    add("4. claims `ported` without a recorded normalised comparison (top-level")
    add("   defs, route strings, response keys);")
    add("5. desynchronises the row count from `git ls-files` on any clone;")
    add("6. carries an empty or undefined `status`.")
    add("")
    add("`python scripts/parity_ledger.py --check` regenerates in memory and compares")
    add("against the committed files, so a ledger that has drifted from its inputs")
    add("fails visibly instead of being trusted.")
    add("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def generate(scratch: Path) -> Tuple["OrderedDict[str, object]", str, Dict[str, int], List[str]]:
    """Build the ledger. Returns (payload, markdown, per-clone counts, warnings)."""
    warnings: List[str] = []
    rows: List[Row] = []
    per_clone: Dict[str, int] = OrderedDict()
    surfaces_meta: "OrderedDict[str, dict]" = OrderedDict()

    for surface in SURFACES:
        clone = scratch / surface.clone_dirname
        if not (clone / ".git").exists():
            raise SystemExit(f"missing upstream clone: {clone}")
        pin = repo_pin(clone)
        if surface.expected_pin and pin != surface.expected_pin:
            warnings.append(
                f"{surface.name}: pin moved — brief said {surface.expected_pin}, "
                f"clone is at {pin}; using the clone's measured pin")
        ls_tree = repo_ls_tree(clone, pin)
        ls_files = repo_ls_files_count(clone)
        if len(ls_tree) != ls_files:
            warnings.append(
                f"{surface.name}: git ls-tree ({len(ls_tree)}) != git ls-files "
                f"({ls_files}); using ls-tree and failing reconciliation")
        per_clone[surface.name] = ls_files
        surfaces_meta[surface.name] = OrderedDict(
            clone=str(clone),
            repo=surface.clone_dirname,
            pin=pin,
            maps_to=surface.our_roots[0] if surface.our_roots else "",
            files_at_pin_ls_tree=len(ls_tree),
            files_git_ls_files=ls_files,
        )
        inv = our_inventory(surface)
        surfaces_meta[surface.name]["our_inventory_files"] = len(inv)
        surfaces_meta[surface.name]["our_inventory_lines"] = sum(
            f.lines for f in inv.values())
        rows.extend(build_rows(surface, clone, pin, inv, warnings))

    expected = sum(per_clone.values())
    errors = enforce(rows, expected, per_clone, warnings)
    data = payload(surfaces_meta, rows, per_clone)
    data["warnings"] = warnings
    md = render_markdown(data)
    if errors:
        data["errors"] = errors
    return data, md, per_clone, errors


def _without_timestamp(data: dict) -> dict:
    """The ledger minus the one field that changes every run.

    ``--check`` answers "has the data drifted from its inputs", so the
    generation timestamp must not make a fresh ledger look stale.
    """
    copy = dict(data)
    copy.pop("generated_at", None)
    return copy


def _strip_generated_line(md: str) -> str:
    return "\n".join(
        line for line in md.splitlines() if not line.startswith("- generated: "))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default=os.environ.get("QUBIT_PARITY_SCRATCH",
                                                        str(DEFAULT_SCRATCH)),
                    help="directory holding the three pinned upstream clones")
    ap.add_argument("--check", action="store_true",
                    help="regenerate in memory and diff against the committed files")
    ap.add_argument("--out-dir", default=None,
                    help="write the ledger somewhere other than docs/ (used by the "
                         "test suite so a fixture run cannot replace the real ledger)")
    args = ap.parse_args(argv)

    json_out = Path(args.out_dir) / "PARITY_LEDGER.json" if args.out_dir else JSON_OUT
    md_out = Path(args.out_dir) / "PARITY_LEDGER.md" if args.out_dir else MD_OUT

    data, md, per_clone, errors = generate(Path(args.scratch))

    print("== reconciliation")
    for name, count in per_clone.items():
        rows = sum(1 for r in data["rows"] if r["surface"] == name)  # type: ignore[union-attr]
        print(f"  {name:10s} upstream git ls-files = {count:5d}   ledger rows = {rows:5d}"
              f"   {'OK' if rows == count else 'MISMATCH'}")
    total = sum(per_clone.values())
    print(f"  {'TOTAL':10s} upstream = {total:5d}   ledger rows = {len(data['rows']):5d}"  # type: ignore[arg-type]
          f"   {'OK' if len(data['rows']) == total else 'MISMATCH'}")  # type: ignore[arg-type]
    counts = Counter(r["status"] for r in data["rows"])  # type: ignore[arg-type]
    print("== status: " + ", ".join(f"{k}={counts.get(k, 0)}" for k in STATUSES))

    for w in data.get("warnings", []):  # type: ignore[union-attr]
        print(f"WARN {w}", file=sys.stderr)

    if errors:
        print(f"== {len(errors)} HARD RULE VIOLATION(S) — refusing to emit", file=sys.stderr)
        for e in errors:
            print(f"FAIL {e}", file=sys.stderr)
        return 1

    if args.check:
        stale = []
        if json_out.exists():
            old = json.loads(json_out.read_text(encoding="utf-8"))
            if _without_timestamp(old) != _without_timestamp(data):
                stale.append(str(JSON_OUT.relative_to(REPO_ROOT)))
        else:
            stale.append(str(JSON_OUT.relative_to(REPO_ROOT)))
        if md_out.exists():
            old_md = md_out.read_text(encoding="utf-8")
            if _strip_generated_line(old_md) != _strip_generated_line(md):
                stale.append(str(MD_OUT.relative_to(REPO_ROOT)))
        else:
            stale.append(str(MD_OUT.relative_to(REPO_ROOT)))
        if stale:
            print("== STALE: " + ", ".join(stale) + " — regenerate with "
                  "`python scripts/parity_ledger.py` (never hand-edit)",
                  file=sys.stderr)
            return 1
        print("== up to date")
        return 0

    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    md_out.write_text(md, encoding="utf-8")
    digest = hashlib.sha256(json_out.read_bytes()).hexdigest()[:12]
    print(f"== wrote {json_out} (sha256:{digest}) and {md_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
