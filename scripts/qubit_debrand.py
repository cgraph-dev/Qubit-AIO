#!/usr/bin/env python3
"""Classified Qubit content de-branding engine (owner amendment 2026-09-19).

Second phase of the deep rebrand. The structural engine
(``scripts/qubit_rebrand.py``) renamed the vendored package, the top-level
modules, the Qubit-owned ``voicebridge`` modules and ``HERMES_HOME``. This
engine removes the remaining *user-facing* upstream branding from the shipped
product source: prose in comments/docstrings, log/error messages, prompts and
UI strings (``Hermes`` -> ``Qubit``, ``Nous Research``/``Nous`` -> ``Qubit
Labs``, ``OpenClaw`` -> ``Qubit``).

It is deliberately **not** a blind replace and never renames code identifiers:

* only **standalone brand words** are rewritten (``\\bHermes\\b``), so
  identifiers that merely contain the word (``get_qubit_home``,
  ``HermesCLI``, ``synchronous``) are untouched;
* environment names (``HERMES_*``), dotted wire identifiers
  (``hermes.task_run.started``, ``openclaw.dispatch``), hyphenated plugin /
  service / command ids (``hermes-kanban``), URLs, e-mail addresses and social
  handles are masked before the transform and restored after;
* legal/provenance/comparison names and paths are preserved;
* protected trees (licenses, provenance manifests, pristine upstream source,
  comparison records, provenance tooling) are never opened;
* dry-run by default; ``--apply`` rewrites; idempotent.

Usage:
    python scripts/qubit_debrand.py                 # classify, print summary
    python scripts/qubit_debrand.py --apply         # rewrite (idempotent)
    python scripts/qubit_debrand.py --report-only   # regenerate manifest only
    python scripts/qubit_debrand.py --report OUT    # write classification JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------
#: Shipped product source. Provenance tooling (``scripts/``) and the guard
#: suite (``tests/``) retain upstream project identifiers on purpose and are
#: never rewritten by this engine (they reference the pinned upstream by name).
SOURCE_ROOTS = [
    "qubit_runtime",
    "voicebridge",
    "frontend/qubit-web/src",
    "frontend/qubit-shared/src",
    "frontend/qubit-app",
]
SOURCE_FILES = [
    "config/voice.example.yaml", "docker-compose.yml",
]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next",
             "coverage", ".turbo"}

#: Trees that are legal/provenance or non-shipped upstream material.
PROTECTED_PARTS = (
    "LICENSE", "THIRD_PARTY_NOTICES", "docs/archive/2026-09-21/LICENSING", "docs/archive/2026-09-21/UPSTREAM_REUSE",
    "voicebridge/upstream",
    "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST", "docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON",
    "docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY", "docs/archive/2026-09-21/INVENTORY_",
    "docs/archive/2026-09-21/QUBIT_GAP_LEDGER", "docs/archive/2026-09-21/AGENT_PROMPT_PACK",
    "scripts/qubit_rebrand.py", "scripts/qubit_debrand.py",
    "docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST", "docs/archive/2026-09-21/QUBIT_DEBRAND_MANIFEST",
    "qubit_runtime/tests", "qubit_runtime/apps", "qubit_runtime/website",
    "qubit_runtime/contributors", "qubit_runtime/evals", "qubit_runtime/docs",
    "qubit_runtime/optional-mcps", "qubit_runtime/native/docs",
)
TEXT_SUFFIXES = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                 ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sh",
                 ".bash", ".service", ".timer", ".md", ".html", ".css",
                 ".nix", ".ps1"}

# ---------------------------------------------------------------------------
# classification rules
# ---------------------------------------------------------------------------
#: Exact compatibility / legal identifiers that survive verbatim.
PRESERVE_STRINGS = (
    "__HERMES_BASE_PATH__", "__HERMES_SESSION_TOKEN__",
    "__HERMES_AUTH_REQUIRED__", "__HERMES_PLUGINS__",
    "__HERMES_PLUGIN_SDK__", "X-Hermes-Session-Token",
    "hermes_flow", "hermes.tokenReloadAttempted",
    "hermes-kanban", "hermes-flow", "hermes_achievements",
    "hermes-achievements", "HERMES_QWEN_", "qubit-hermes",
)
#: Standalone prose words -> replacement (Word-boundary, case-insensitive).
PROSE_WORDS = {
    "hermes": "qubit",
    "nous": "qubit",
    "openclaw": "qubit",
}
#: Multi-word prose phrases applied before single words.
PHRASE_MAP = {
    "Nous Research": "Qubit Labs",
    "nous research": "qubit labs",
    "NOUS RESEARCH": "QUBIT LABS",
    "Nous Portal": "Qubit Labs",
    "nous portal": "qubit labs",
    "NOUS PORTAL": "QUBIT LABS",
}
#: Spans that must never be touched.
PROTECT_SPAN = re.compile(
    r"https?://[^\s\"'<>)\]]+"           # URLs
    r"|git@[^\s\"']+"                     # git remotes
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"    # e-mail
    r"|@[A-Za-z0-9_]+"                    # handles
)
#: Dotted wire identifiers (metrics, events, object types, service ids,
#: logger names) and lowercase hyphenated plugin/service ids.
_WIRE_DOTTED = re.compile(
    r"\b(?:hermes|Hermes|HERMES|nous|Nous|NOUS|openclaw|OpenClaw|OPENCLAW)"
    r"[A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)+")
_WIRE_HYPHEN = re.compile(
    r"\b(?:hermes|openclaw|nous)-[a-z0-9][a-z0-9_-]*")
#: Bare quoted lower-case ids used as provenance/source names.
_QUOTED_ID = re.compile(r"""["'](?:hermes|openclaw|nous)["']""")
#: A hardcoded upstream home path is a migration/external source, not branding.
_DOT_HERMES = re.compile(r"\.hermes\b")
#: Legal attribution lines (MIT/Apache notices naming the upstream author) are
#: provenance and must survive verbatim.
_LEGAL_LINE = re.compile(r"(?:Copyright|©|\(c\)\s*\d{4})[^\n]*")

_WORD = re.compile(r"\b(Hermes|HERMES|hermes|Nous|NOUS|nous|OpenClaw|"
                   r"OPENCLAW|openclaw)\b")


def _protected(rel: str) -> bool:
    return any(p in rel for p in PROTECTED_PARTS)


def _case_like(src: str, dst: str) -> str:
    if src.isupper():
        return dst.upper()
    if src[:1].isupper():
        return dst.capitalize()
    return dst


def _classify_word(m: re.Match) -> str:
    return _case_like(m.group(1), PROSE_WORDS[m.group(1).lower()])


def _transform(text: str) -> tuple[str, list[str]]:
    """Return (new_text, classes_touched)."""
    masks: list[str] = []
    classes: set[str] = set()

    def _mask(m: re.Match) -> str:
        masks.append(m.group(0))
        return f"\x00{len(masks) - 1}\x00"

    protected = PROTECT_SPAN.sub(_mask, text)
    protected = _WIRE_DOTTED.sub(_mask, protected)
    protected = _WIRE_HYPHEN.sub(_mask, protected)
    protected = _QUOTED_ID.sub(_mask, protected)
    protected = _DOT_HERMES.sub(_mask, protected)
    protected = _LEGAL_LINE.sub(_mask, protected)
    for s in PRESERVE_STRINGS:
        if s in protected:
            protected = protected.replace(s, _mask(re.match(re.escape(s), s)))
    for src, dst in PHRASE_MAP.items():
        if src in protected:
            protected = protected.replace(src, dst)
            classes.add("phrase")
    new = _WORD.sub(_classify_word, protected)
    if new != protected:
        classes.add("prose-word")

    def _restore(m: re.Match) -> str:
        return masks[int(m.group(1))]

    new = re.sub(r"\x00(\d+)\x00", _restore, new)
    return new, sorted(classes)


def _iter_files():
    for root in SOURCE_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                p = Path(dirpath) / f
                rel = p.relative_to(REPO).as_posix()
                if p.suffix in TEXT_SUFFIXES and not _protected(rel):
                    yield p, rel
    for rel in SOURCE_FILES:
        p = REPO / rel
        if p.is_file() and not _protected(rel):
            yield p, rel


def scan() -> dict:
    files = changed = 0
    tokens: dict[str, int] = {}
    surface: dict[str, int] = {}
    for p, rel in _iter_files():
        files += 1
        try:
            text = p.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        new, classes = _transform(text)
        if new != text:
            changed += 1
        # remaining standalone brand words after the pass (should trend to 0)
        for m in _WORD.finditer(new):
            t = m.group(0)
            tokens[t] = tokens.get(t, 0) + 1
        # Detection-only branded tokens the prose pass never rewrites (compound
        # NousResearch org token, branded upstream URLs, branded service names).
        for line in new.splitlines():
            if _LEGAL_LINE.search(line):
                continue
            for tok, _cls in _surface_tokens(line):
                surface[tok] = surface.get(tok, 0) + 1
    return {"files_scanned": files, "files_changed": changed,
            "remaining_words": dict(sorted(tokens.items(),
                                           key=lambda kv: -kv[1])),
            "remaining_surface": dict(sorted(surface.items(),
                                             key=lambda kv: -kv[1]))}


def apply(report_out: Path | None, *, write_sources: bool = True) -> int:
    changed = 0
    class_counts: dict[str, int] = {}
    for p, rel in _iter_files():
        try:
            text = p.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        new, classes = _transform(text)
        if new != text:
            if write_sources:
                p.write_text(new, encoding="utf-8", errors="surrogateescape")
            changed += 1
            for c in classes:
                class_counts[c] = class_counts.get(c, 0) + 1
    report = {
        "schema": "qubit-debrand/1",
        "amendment": "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md",
        "prose_words": PROSE_WORDS,
        "phrase_map": PHRASE_MAP,
        "preserve_strings": list(PRESERVE_STRINGS),
        "protected_parts": list(PROTECTED_PARTS),
        "files_changed": changed,
        "class_counts": class_counts,
    }
    if report_out:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(
            json.dumps(report, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    print(f"debrand: files changed={changed}"
          + ("" if write_sources else " (report-only; no source rewritten)"))
    return 0


_BRAND_TOKEN = re.compile(
    r"[A-Za-z0-9_./@~-]*(?:hermes|Hermes|HERMES|nous|Nous|NOUS"
    r"|openclaw|OpenClaw|OPENCLAW)[A-Za-z0-9_./@~-]*")
_ENV_TOKEN = re.compile(r"\bHERMES_[A-Z0-9_]+\b")

# ---------------------------------------------------------------------------
# detection-only patterns (never rewritten)
# ---------------------------------------------------------------------------
# ``_WORD`` uses ``\bNous\b``, which does NOT match the compound org token
# ``NousResearch`` (the ``N``/``R`` are both word characters, so there is no
# word boundary). These patterns are used for *detection and reporting* only:
# they are deliberately excluded from the transform so that genuine provenance
# identifiers (plugin repo slugs like ``NousResearch/hermes-media-studio``, the
# pinned upstream URLs, env names, service files) are surfaced for triage but
# never silently rewritten. See the module docstring: rewrite only rendered
# prose; report the rest.
_NOUSRESEARCH = re.compile(r"\bNousResearch\b")
_UPSTREAM_URL = re.compile(
    r"https?://[^\s\"'<>)\]]*(?:nousresearch|hermes-agent)[^\s\"'<>)\]]*",
    re.IGNORECASE)
_BRAND_SERVICE = re.compile(
    r"\b(?:hermes|openclaw|nous)[-_](?:agent|acp|update|gateway|cli|service|"
    r"desktop|dashboard|web|tui|kanban|proxy|relay|portal|home|flow)"
    r"\b(?:\.(?:service|exe|ps1|sh|cmd|timer))?", re.IGNORECASE)


def _surface_tokens(line: str) -> list[tuple[str, str]]:
    """Detection-only branded tokens the prose pass intentionally leaves alone.

    Returns ``(token, class)`` pairs for the compound ``NousResearch`` org
    token, branded upstream URLs, and branded service/artifact names (e.g.
    ``hermes-acp``, ``hermes-gateway.service``). These are reported so the
    public CLI surface can be certified without rewriting wire identifiers.
    """
    out: list[tuple[str, str]] = []
    for rx, cls in ((_UPSTREAM_URL, "upstream_url"),
                    (_BRAND_SERVICE, "brand_service_name"),
                    (_NOUSRESEARCH, "nousresearch_org")):
        for m in rx.finditer(line):
            out.append((m.group(0), cls))
    return out


def _class_of(token: str, line: str) -> str:
    """Classify one brand-bearing token by its compatibility disposition."""
    if _ENV_TOKEN.fullmatch(token):
        return "env_name"
    if re.search(r"(?:^|[A-Za-z0-9_])[.\-][A-Za-z0-9_]*(?:hermes|nous|openclaw)",
                 token, re.I):
        return "wire_dotted_or_hyphen"
    if "/" in token or token.startswith(".") or token.startswith("~"):
        return "path_literal"
    if re.fullmatch(r"[A-Za-z0-9_]*(?:hermes|Hermes|HERMES|nous|Nous|NOUS"
                    r"|openclaw|OpenClaw|OPENCLAW)[A-Za-z0-9_]*", token):
        # A word-boundary standalone word vs an embedded identifier segment.
        core = token.lower()
        for brand in ("hermes", "nous", "openclaw"):
            if core == brand:
                return "prose_word"
        return "identifier"
    return "other"


def classify(report_out: Path) -> int:
    """Bucket every remaining upstream-name occurrence by disposition."""
    classes: dict[str, int] = {}
    tokens: dict[str, dict[str, int]] = {}
    samples: dict[str, list[str]] = {}
    for p, rel in _iter_files():
        try:
            lines = p.read_text(encoding="utf-8",
                                errors="surrogateescape").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if _LEGAL_LINE.search(line):
                classes["legal_attribution"] = classes.get(
                    "legal_attribution", 0) + 1
                continue
            for m in _BRAND_TOKEN.finditer(line):
                tok = m.group(0)
                cls = _class_of(tok, line)
                classes[cls] = classes.get(cls, 0) + 1
                tokens.setdefault(cls, {})[tok] = \
                    tokens.get(cls, {}).get(tok, 0) + 1
                bucket = samples.setdefault(cls, [])
                if len(bucket) < 5:
                    bucket.append(f"{rel}:{lineno} {tok}")
    report = {
        "schema": "qubit-debrand-classification/1",
        "amendment": "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md",
        "classes": dict(sorted(classes.items())),
        "top_tokens": {
            c: dict(sorted(t.items(), key=lambda kv: -kv[1])[:20])
            for c, t in sorted(tokens.items())
        },
        "samples": {c: samples[c] for c in sorted(samples)},
        "preserve_strings": list(PRESERVE_STRINGS),
        "protected_trees": list(PROTECTED_PARTS),
        "note": "Every occurrence not auto-rewritten by the prose pass is "
                "classified here: wire/dotted/hyphen/quote/env/path are "
                "preserved compatibility identifiers; identifier occurrences "
                "are the documented internal vendor source deferred to the "
                "separate module-graph migration; legal_attribution lines are "
                "provenance and preserved. Detection-only branded tokens "
                "(compound NousResearch, upstream URLs, service/artifact "
                "names) are reported separately by `--detect` and never "
                "rewritten here.",
    }
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    print(f"classification: {report['classes']}")
    print(f"wrote {report_out}")
    return 0


def detect(report_out: Path | None = None) -> int:
    """Report detection-only branded tokens with exact file:line locations.

    This is the certification pass for the public CLI surface: it surfaces the
    compound ``NousResearch`` org token (invisible to the word-boundary prose
    pass), branded upstream URLs, and branded service/artifact names. It never
    rewrites; the caller triages each hit and de-brands only the rendered
    strings, leaving wire/provenance identifiers intact.
    """
    hits: dict[str, dict[str, int]] = {}
    by_class: dict[str, int] = {}
    for p, rel in _iter_files():
        try:
            lines = p.read_text(encoding="utf-8",
                                errors="surrogateescape").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if _LEGAL_LINE.search(line):
                continue
            for tok, cls in _surface_tokens(line):
                key = f"{rel}:{lineno}"
                bucket = hits.setdefault(key, {})
                bucket[tok] = bucket.get(tok, 0) + 1
                by_class[cls] = by_class.get(cls, 0) + 1
    report = {
        "schema": "qubit-debrand-surface/1",
        "amendment": "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md",
        "detectors": {
            "nousresearch_org": _NOUSRESEARCH.pattern,
            "upstream_url": _UPSTREAM_URL.pattern,
            "brand_service_name": _BRAND_SERVICE.pattern,
        },
        "counts_by_class": dict(sorted(by_class.items())),
        "hits": hits,
        "note": "Detection only — nothing here is auto-rewritten. Triage each "
                "hit: a printed/rendered string must be de-branded in source; a "
                "plugin repo slug, pinned upstream URL, env name or service "
                "file is a wire/provenance identifier and stays verbatim.",
    }
    if report_out:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
    print(f"surface detection: {report['counts_by_class']} "
          f"({len(hits)} file:line sites)")
    if report_out:
        print(f"wrote {report_out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report-only", action="store_true",
                    help="write the manifest without rewriting any source file "
                         "(the deterministic report-only regeneration mode)")
    ap.add_argument("--classify", action="store_true")
    ap.add_argument("--detect", action="store_true",
                    help="report detection-only branded tokens (NousResearch "
                         "org token, upstream URLs, service/artifact names) with "
                         "file:line locations; never rewrites")
    ap.add_argument("--report", type=Path,
                    default=REPO / "docs/archive/2026-09-21" / "QUBIT_DEBRAND_MANIFEST.json")
    args = ap.parse_args(argv)
    if args.apply:
        return apply(args.report)
    if args.report_only:
        return apply(args.report, write_sources=False)
    if args.classify:
        return classify(REPO / "docs/archive/2026-09-21" / "QUBIT_DEBRAND_CLASSIFICATION.json")
    if args.detect:
        return detect()
    result = scan()
    print(f"DRY RUN: files scanned={result['files_scanned']} "
          f"files would change={result['files_changed']}")
    print("remaining standalone brand words after pass:")
    for tok, n in list(result["remaining_words"].items())[:40]:
        print(f"  {n:6d}  {tok}")
    print("detection-only surface tokens (not rewritten; triage rendered sites):")
    for tok, n in list(result["remaining_surface"].items())[:40]:
        print(f"  {n:6d}  {tok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
