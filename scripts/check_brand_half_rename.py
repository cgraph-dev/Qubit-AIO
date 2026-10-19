#!/usr/bin/env python3
"""BRAND HALF-RENAME CHECK: a `__QUBIT_*__` / `__HERMES_*__` global that one
side writes and the other side reads under a different spelling.

WHY THIS EXISTS
---------------
The desktop handshake broke exactly this way: `web_server_dashboard.py` (the
`qubit serve` backend) writes `window.__HERMES_SESSION_TOKEN__=...`, while the
shell's token extractor and the SPA both read `__QUBIT_SESSION_TOKEN__`. Neither
side is wrong alone, no test in either language can see the other side, and a
text grep finds BOTH spellings -- so a "does the name exist?" audit reports
present while the running system has no credential. That is a half-rename.

THE RULE THIS ENFORCES
----------------------
`__QUBIT_X__` and `__HERMES_X__` are one name in two brand spellings. For each
suffix X, at least one spelling must be BOTH written and read in the tree.

  OK    a written spelling is also read somewhere          (self-consistent)
  OK    a spelling that is read but never written           (documented alias: the
                                                            plugin SDK legacy globals
                                                            are written by external,
                                                            unrebuildable plugin `dist`
                                                            bundles -- see
                                                            frontend/qubit-web/src/
                                                            plugins/plugin-globals.test.ts)
  BAD   X is WRITTEN under a spelling nothing in the tree  (the desktop-auth class)
        reads, while the other spelling IS read

WHAT IS NOT A WRITE OR A READ
-----------------------------
- Comments and docstrings, in Python and in JS/TS. Prose is not an assignment; a
  prior manual audit was fooled by a name inside a comment.
- A non-injectable Python string literal. `window.__X__=...` inside a string IS
  a write (the server injects it); a bare `__X__` inside an f-string or docstring
  quote is not, unless the literal is plainly an injected page fragment.
- Every match is bounded by `__` at both ends, so `__QUIBIT_A__` never
  conflates with `__QUIBIT_AUTH_REQUIRED__`.
- A "read" is any non-comment, non-docstring mention: a regex, a TS assertion, a
  JS substring check. That is deliberate -- the reader in the desktop bug WAS a
  regex (apps/desktop/electron/dashboard-token.ts).

OVERRIDES
---------
`# brand-half-rename: writes|reads|ignore <SUFFIX>` on a line (or the line
above) declares intent where the counterpart lives outside this repo. Use it
sparingly and say why in the comment.

Usage
-----
    python3 scripts/check_brand_half_rename.py                    # scan the repo
    python3 scripts/check_brand_half_rename.py --root DIR         # scan any tree
    python3 scripts/check_brand_half_rename.py --root DIR --json out.json
    python3 scripts/check_brand_half_rename.py -v                 # list every site

Exit 0 when every suffix is consistent, 1 when any mismatch is found, 2 on a
usage error. CI: `python3 scripts/check_brand_half_rename.py`.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tokenize
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent

#: The two brand spellings of one name. Canonical first.
BRANDS: Tuple[str, str] = ("QUBIT", "HERMES")
NAME_RE = re.compile(r"__(QUBIT|HERMES)_([A-Za-z0-9_]+?)__")
OVERRIDE_RE = re.compile(r"brand-half-rename:\s*(writes|reads|ignore)\s+([A-Za-z0-9_]+)")

SCAN_SUFFIXES = frozenset(
    (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".htm",
     ".sh", ".bash", ".vue", ".svelte")
)
SKIP_DIRS = frozenset((
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "website", "_private_backup",
    ".next", "coverage", ".turbo", "qubit-dist", "sandbox",
))
#: Vendored / generated files carry the upstream spelling by construction.
SKIP_FILE_SUFFIXES = (".min.js", ".min.css", ".map", ".lock")
#: A Python string literal that is plainly an HTML/JS fragment injected into a page.
INJECTABLE_HINTS = ("window.", "<script", "document.")

#: An override comment is only an override if it sits in a real comment. Prose in a
#: docstring that happens to contain the marker phrase must not silently suppress a
#: finding -- the docstring below quotes the syntax, so it is stripped from the lines
#: searched for overrides.
OVERRIDE_LINE_RE = re.compile(r"^\s*(#|//|/\*|\*)")

#: Test files SIMULATE a server: `window.__X__ = "tok"` in a test fixture is a
#: stand-in for the writer, not the writer. Counting those as production writers
#: hides the real mismatch (dashboard-token.test.ts stubs the canonical spelling,
#: which made the live `web_server_dashboard.py` split invisible). Test writers are
#: kept in the report but only satisfy a spelling as a LAST resort, and the problem
#: text says so.
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|e2e|evals)/"
    r"|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$"
)


@dataclass
class Site:
    path: str
    line: int
    brand: str
    suffix: str
    kind: str        # "write" | "read"
    why: str
    is_test: bool = False


@dataclass
class Repo:
    writes: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    reads: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    sites: List[Site] = field(default_factory=list)
    overrides: List[Tuple[str, str, str, int]] = field(default_factory=list)


def iter_files(root: Path) -> Iterator[Path]:
    root_str = str(root)
    for dirpath, dirs, files in os.walk(root_str):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.startswith("."):
                continue
            if f.endswith(SKIP_FILE_SUFFIXES):
                continue
            p = Path(dirpath) / f
            if p.suffix.lower() in SCAN_SUFFIXES:
                yield p


#: Directories holding a SPA source tree. A page served from a bundle in this
#: tree runs THAT bundle's code, so the bundle's own reads are what the injected
#: global hits -- a read in another SPA tree does not rescue it.
SPA_TREE_RE = re.compile(r"(^|/)qubit_runtime/web/|(^|/)frontend/qubit-web/")


def _spa_tree(rel: str) -> str:
    mt = SPA_TREE_RE.search(rel)
    if not mt:
        return ""
    return rel[:mt.end()].rstrip("/")


def _is_write_rhs(after: str, *, in_string: bool = False, shell_ok: bool = False) -> bool:
    """True when the text following a global name starts its assignment RHS.

    `window.__X__={...}` / `window.__X__="..."` / `__X__ = value` are writes.
    `"token_source": "/chat window.__X__"` is NOT: the `=` sits inside its own
    quoted string. Inside a string literal an assignment RHS must be clearly
    produced content -- `=#{` (shell heredoc) or `="` -- so a matcher regex such as
    `r'window\\.__X__="(\\d+)"'` stays a reader.
    """
    rest = after.lstrip()
    if rest.startswith("==") or not rest.startswith("="):
        return False
    rhs = rest[1:].lstrip()
    if not rhs:
        return True
    if rhs[0] == ":":
        return False  # TS type annotation
    if in_string:
        # Inside a literal only clearly produced payload is a write; an `=""` in a
        # matcher/assertion operand (`b'__X__=""'`) is the needle, not the output.
        return rhs.startswith("#{")
    # A quoted value is a produced payload everywhere, and `(real` is a TS cast
    # (`window.__X__ as T`). `{`/`[` are only an assignment in a language where the
    # `=` is not a pattern: an unquoted `=` in shell is argument to `[`/`test`, and
    # `=~` is a match. Callers pass ``shell_ok=False`` for those.
    if rhs[0] in "\"'":
        return True
    if shell_ok:
        return False
    return rhs[0] in "{[" or rhs.startswith("#{")


#: Shell: an unquoted `=` after a word is an argument to `[`/`test` (`[[ $x = a ]]`)
#: or a regex match (`=~`), never an assignment.
SHELL_SUFFIXES = frozenset((".sh", ".bash"))

#: A build-time HTML transform (vite `transformIndexHtml` / `injectTo:`) injects a
#: global into a page it is building. It is neither the server that renders the page
#: nor the bundle that runs in it, so it is not a site on the writer/reader pair.
HTML_INJECT_TRANSFORM_RE = re.compile(
    r"injectTo|transformIndexHtml|transformAssetUrls")


def _classify_code(rel: str, lineno: int, code: str, src_window: str, repo: Repo) -> None:
    """Classify every global NAME_RE finds on one line of real code."""
    is_test = bool(TEST_PATH_RE.search(rel))
    shell_ok = Path(rel).suffix.lower() in SHELL_SUFFIXES
    for mt in NAME_RE.finditer(code):
        brand, suffix = mt.group(1), mt.group(2)
        is_write = _is_write_rhs(code[mt.end():mt.end() + 6], shell_ok=shell_ok)
        repo.sites.append(Site(rel, lineno, brand, suffix,
                               "write" if is_write else "read",
                               "assignment" if is_write else "reference", is_test))


def _docstring_positions(src: str) -> Set[Tuple[int, int]]:
    """(line, col) of every docstring token -- module, class and function."""
    import ast

    positions: Set[Tuple[int, int]] = set()
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return positions
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            positions.add((first.value.lineno, first.value.col_offset))
    return positions


def classify_python(rel: str, src: str, repo: Repo) -> None:
    """Tokenize; STRING tokens are treated specially, everything else is code."""
    lines = src.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks = []
    if not toks:
        # Unparseable: fall back to a crude comment strip (never count prose as a write).
        for lineno, raw in enumerate(lines, 1):
            _classify_code(rel, lineno, raw.split("#", 1)[0], raw, repo)
        return

    docstrings = _docstring_positions(src)
    str_toks: Dict[int, List[Tuple[int, str, bool]]] = defaultdict(list)
    for tok in toks:
        if tok.type == tokenize.STRING:
            is_doc = (tok.start[0], tok.start[1]) in docstrings
            str_toks[tok.start[0]].append((tok.start[1], tok.string, is_doc))
        elif tok.type == tokenize.COMMENT:
            # Blank the comment out of its own line so nothing inside it counts.
            row = tok.start[0]
            lines[row - 1] = lines[row - 1][:tok.start[1]]
    for lineno, raw in enumerate(lines, 1):
        if lineno in str_toks:
            # Blank every literal on the line. A match inside a literal is a
            # WRITE only when it is an assignment target in injected markup;
            # everything else (a reader's regex, a "retired globals" list, an
            # assertion) is a reference.
            spans = sorted(str_toks[lineno])
            window = "\n".join(lines[max(0, lineno - 2):lineno])
            injectable = any(h in window for h in INJECTABLE_HINTS)
            for col, tok_str, is_doc in spans:
                for mt in NAME_RE.finditer(tok_str):
                    brand, suffix = mt.group(1), mt.group(2)
                    if is_doc:
                        continue  # prose in a docstring is neither a write nor a read
                    is_test = bool(TEST_PATH_RE.search(rel))
                    after = tok_str[mt.end():mt.end() + 6]
                    if injectable and _is_write_rhs(after, in_string=True):
                        repo.sites.append(Site(rel, lineno, brand, suffix, "write",
                                               "injected JS payload", is_test))
                    else:
                        repo.sites.append(Site(rel, lineno, brand, suffix, "read",
                                               "string literal", is_test))
            # Now blank the literals so the code-only pass below sees code only.
            code_parts: List[str] = []
            cursor = 0
            for col, tok_str, _is_doc in spans:
                if col < cursor:
                    continue
                code_parts.append(raw[cursor:col])
                cursor = col + len(tok_str)
            code_parts.append(raw[cursor:])
            raw = "".join(code_parts)
        _classify_code(rel, lineno, raw, raw, repo)


def _strip_js_line(raw: str, in_block: bool) -> Tuple[str, bool]:
    """Remove // and /* */ comments from one JS/TS line. Returns (code, in_block)."""
    out: List[str] = []
    i, n = 0, len(raw)
    while i < n:
        if in_block:
            end = raw.find("*/", i)
            if end < 0:
                return "".join(out), True
            i = end + 2
            in_block = False
            continue
        if raw.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if raw.startswith("//", i):
            break
        ch = raw[i]
        if ch in "\"'`":
            # Skip the literal verbatim: a URL inside it has no `//` comment.
            j = i + 1
            while j < n:
                if raw[j] == "\\":
                    j += 2
                    continue
                if raw[j] == ch:
                    j += 1
                    break
                j += 1
            else:
                return "".join(out), False
            out.append(raw[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out), in_block


def _quoted_spans(line: str) -> List[Tuple[int, int]]:
    """Character spans of quoted literal bodies on one line (best effort)."""
    spans: List[Tuple[int, int]] = []
    # Single quotes too: a shell assertion (`assert b'__X__=""' in body`) puts the
    # name in a single-quoted literal, and that is copy for a matcher, not a write.
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch in "\"'`":
            start = i + 1
            j = start
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == ch:
                    break
                j += 1
            spans.append((start, min(j, n)))
            i = j + 1
            continue
        i += 1
    return spans


def _classify_text_line(rel: str, lineno: int, code: str, repo: Repo) -> None:
    """Classify a non-Python source line, knowing which matches sit inside strings."""
    is_test = bool(TEST_PATH_RE.search(rel))
    spans = _quoted_spans(code)
    for mt in NAME_RE.finditer(code):
        brand, suffix = mt.group(1), mt.group(2)
        in_string = any(a <= mt.start() < b for a, b in spans)
        is_write = _is_write_rhs(code[mt.end():mt.end() + 6], in_string=in_string)
        if in_string and not is_write:
            why = "string literal"
        else:
            why = "assignment" if is_write else "reference"
        repo.sites.append(Site(rel, lineno, brand, suffix,
                               "write" if is_write else "read", why, is_test))


def classify_text(rel: str, src: str, repo: Repo) -> None:
    in_block = False
    for lineno, raw in enumerate(src.splitlines(), 1):
        code, in_block = _strip_js_line(raw, in_block)
        _classify_text_line(rel, lineno, code, repo)


def scan(root: Path) -> Repo:
    repo = Repo()
    for path in iter_files(root):
        try:
            src = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, raw in enumerate(src.splitlines(), 1):
            m_ov = OVERRIDE_RE.search(raw)
            if m_ov:
                repo.overrides.append((rel, m_ov.group(1), m_ov.group(2), lineno))
        if HTML_INJECT_TRANSFORM_RE.search(src) and path.suffix.lower() != ".py":
            # A vite plugin that rewrites the HTML it builds injects a global into the
            # page as a transform, not as a server render. Its `=` sits inside a
            # template literal of that transform, so neither side of the pair is here.
            continue
        if path.suffix.lower() == ".py":
            classify_python(rel, src, repo)
        else:
            classify_text(rel, src, repo)
    for site in repo.sites:
        bucket = repo.writes if site.kind == "write" else repo.reads
        brand = site.brand + ("*" if (site.kind == "write" and site.is_test) else "")
        bucket[site.suffix].add(brand)
    return repo


#: The SPA source tree that `qubit serve` SHIPS. `qubit_runtime/web/vite.config.ts`
#: builds into `qubit_cli/web_dist`, which `web_server.py` serves as `WEB_DIST`, so
#: the page this backend renders runs THIS tree's code. A read anywhere else (another
#: SPA tree, a vite plugin regex aimed at a different server, a verifier's assertion,
#: a "retired globals" list in a test) cannot make the served bundle see a name.
SERVED_SPA_TREE = "qubit_runtime/web"


def servable_in_served_spa(site: Site) -> bool:
    """True when *site* is a read by the bundle `qubit serve` actually serves."""
    path = site.path
    if not path.startswith(SERVED_SPA_TREE + "/"):
        return False
    if TEST_PATH_RE.search(path):
        return False
    # vite.config.ts / build tooling does not run in the browser: it inspects HTML.
    return not path.endswith((".config.ts", ".config.js", ".config.mts"))


def evaluate(repo: Repo) -> List[Dict[str, str]]:
    """Problems where a production writer's readers cannot see what it wrote.

    Two shapes, both the M1 class:

    SAME-FILE   one production file writes --A_X-- and reads only --B_X--
                (the write is for a consumer outside that file).
    SERVED-SPA  a production file that renders the page served by the SPA tree in
                `SERVED_SPA_TREE` writes --A_X--, while that tree's own code reads
                only --B_X--. If A is also a name the served tree declares
                (``window.__A_X__?: string``), the contract is proven and the write
                is a bug.
    """
    overrides_ignore = {o[2] for o in repo.overrides if o[1] == "ignore"}
    prod_writes = [s for s in repo.sites if s.kind == "write" and not s.is_test]
    prod_reads = [s for s in repo.sites if s.kind == "read" and not s.is_test]

    def declarations(tree: str) -> Set[str]:
        """Suffixes the served tree declares on `window` (its own read contract)."""
        return {r.suffix for r in repo.sites if servable_in_served_spa(r)}

    declared = declarations(SERVED_SPA_TREE)
    served_reads = [r for r in prod_reads if servable_in_served_spa(r)]

    problems: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()

    by_file: Dict[str, List[Site]] = defaultdict(list)
    for s in prod_writes + prod_reads:
        by_file[s.path].append(s)

    for path, sites in by_file.items():
        local_writes = [s for s in sites if s.kind == "write"]
        local_reads = [s for s in sites if s.kind == "read"]
        for w in local_writes:
            if w.suffix in overrides_ignore:
                continue
            other = "HERMES" if w.brand == "QUBIT" else "QUBIT"
            if any(r.suffix == w.suffix and r.brand == w.brand for r in local_reads):
                continue
            if not any(r.suffix == w.suffix and r.brand == other for r in local_reads):
                continue
            # Same-file evidence needs a witness elsewhere: some other production file
            # must read the other spelling, i.e. the codebase's contract for this name is
            # the other one. Without that, the writer has no reader problem at all.
            if not any(r.suffix == w.suffix and r.brand == other and r.path != path
                       for r in prod_reads):
                continue
            key = ("same-file", w.suffix, w.brand)
            if key in seen:
                continue
            seen.add(key)
            problems.append({
                "shape": "same-file",
                "suffix": w.suffix,
                "written_as": f"__{w.brand}_{w.suffix}__",
                "read_as": f"__{other}_{w.suffix}__",
                "detail": (f"{path} writes __{w.brand}_{w.suffix}__ but only ever reads "
                           f"__{other}_{w.suffix}__ itself."),
            })

    for w in prod_writes:
        if w.suffix in overrides_ignore or w.suffix not in declared:
            continue
        other = "HERMES" if w.brand == "QUBIT" else "QUBIT"
        # The served SPA reads one spelling for this name. If it is the writer's own
        # spelling, the two sides agree. If it is the OTHER spelling, the served page
        # reads a name this writer never provides -- the M1 bug.
        served_brands = {r.brand for r in served_reads if r.suffix == w.suffix}
        if not served_brands or w.brand in served_brands:
            continue
        key = ("served-spa", w.suffix, w.brand)
        if key in seen:
            continue
        seen.add(key)
        problems.append({
            "shape": "served-spa",
            "suffix": w.suffix,
            "written_as": f"__{w.brand}_{w.suffix}__",
            "read_as": f"__{other}_{w.suffix}__",
            "detail": (
                f"{w.path}:{w.line} writes __{w.brand}_{w.suffix}__ for the page, but the "
                f"SPA this backend serves ({SERVED_SPA_TREE}, built to qubit_cli/web_dist) "
                f"reads __{other}_{w.suffix}__ and never __{w.brand}_{w.suffix}__. The "
                f"served page can never see the value."),
        })
    return problems



def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Detect __QUBIT_*__/__HERMES_*__ globals written under one "
                    "brand spelling and read under the other.")
    ap.add_argument("--root", default=str(REPO), help="tree to scan (default: repo root)")
    ap.add_argument("--json", metavar="PATH", help="write the full report as JSON")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every site")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: --root is not a directory: {root}", file=sys.stderr)
        return 2

    repo = scan(root)
    problems = evaluate(repo)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "root": str(root),
            "writes": {k: sorted(v) for k, v in repo.writes.items()},
            "reads": {k: sorted(v) for k, v in repo.reads.items()},
            "overrides": repo.overrides,
            "sites": [s.__dict__ for s in repo.sites],
            "problems": problems,
        }, indent=2), encoding="utf-8")

    if args.verbose:
        for s in sorted(repo.sites, key=lambda s: (s.suffix, s.path, s.line)):
            print(f"  {s.kind:<5} __{s.brand}_{s.suffix}__  {s.path}:{s.line}  ({s.why})")

    if problems:
        print(f"BRAND HALF-RENAME: {len(problems)} mismatched global(s)\n", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p['written_as']}  ->  readers use {p['read_as']}", file=sys.stderr)
            print(f"    {p['detail']}", file=sys.stderr)
            writers = sorted({(s.path, s.line) for s in repo.sites
                              if s.suffix == p["suffix"] and s.kind == "write"
                              and f"__{s.brand}_{s.suffix}__" == p["written_as"]})
            readers = sorted({(s.path, s.line) for s in repo.sites
                              if s.suffix == p["suffix"] and s.kind == "read"
                              and not s.is_test
                              and f"__{s.brand}_{s.suffix}__" == p["read_as"]})
            for path, line in writers:
                print(f"      writer: {path}:{line}", file=sys.stderr)
            for path, line in readers[:6]:
                print(f"      reader: {path}:{line}  -> {p['read_as']}", file=sys.stderr)
            if len(readers) > 6:
                print(f"      … and {len(readers) - 6} more non-test reader site(s)",
                      file=sys.stderr)
            print(file=sys.stderr)
        print("Fix by choosing ONE spelling and changing the writer, the readers and "
              "every test assertion in the same commit. If the counterpart lives "
              "outside this repo, record it with `# brand-half-rename: writes|reads <SUFFIX>`.",
              file=sys.stderr)
        return 1

    print(f"brand half-rename check: OK ({len(set(repo.writes) | set(repo.reads))} "
          f"global name(s) consistent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
