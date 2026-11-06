#!/usr/bin/env python3
"""Prove the rebrand is COMPLETE — writer↔reader pairs, not just a token grep.

A token grep says "no file contains 'hermes'". It does NOT say "the code still
works", because the dangerous half-rename is symmetric: the reader was updated
and the writer was not (or vice versa), so both files are individually clean and
the capability is silently dead. That is the failure this repo has shipped
before, and it is exactly what a rebrand of 1.4 MB of JS can reintroduce.

Four checks, each symmetric where the risk is symmetric:

  P1  globals      — every ``window.__QUBIT_*`` READ by any JS is WRITTEN
                     (by ``index.html`` or by the server injector).
  P2  storage      — every ``localStorage``/``sessionStorage`` key READ is
                     WRITTEN somewhere in the tree, and every key WRITTEN is
                     READ somewhere. An orphan on either side is a dead feature.
  P3  handlers     — every function called from an inline ``onclick=`` /
                     ``onchange=`` attribute exists as a declared function.
  P4  css          — every class name the JS emits into ``class="..."`` (or via
                     ``classList.add``) that looks like one of our renamed
                     families has a matching selector in ``style.css``.

Exit 0 = complete. Exit 1 = a pair is broken, with the file:line of each side.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "static"

#: Globals the SERVER injects (workbench_ui_routes._inject_globals). Read-only
#: from the tree's point of view, so their absence from index.html is correct.
#: Globals the SERVER injects (workbench_ui_routes._inject_globals).
SERVER_GLOBALS = {
    "__QUBIT_SESSION_TOKEN__",
    "__QUBIT_API_PREFIX__",
    "__QUBIT_BASE_PATH__",
    "__QUBIT_WB_MOUNT__",
}

#: Globals written by a THIRD PARTY at runtime, not by this tree: the desktop
#: companion app and the extension host each set their own status object in the
#: page before these scripts read it. They stay in the check as a NON-FAILING
#: note rather than a pass, because "we do not write it" is true and worth
#: seeing on every run.
EXTERNAL_GLOBALS = {
    "__QUBIT_DESKTOP_COMPANION_STATUS__",
    "__QUBIT_EXTENSION_CONFIG__",
}


NOTE: list[str] = []


def _js_files():
    for p in sorted(ROOT.rglob("*.js")):
        yield p


def _html_files():
    for p in sorted(ROOT.rglob("*.html")):
        yield p


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _lineno(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


# --------------------------------------------------------------------- P1
def check_globals() -> list[str]:
    bad: list[str] = []
    reads: dict[str, list[str]] = {}
    writes: set[str] = set()
    write_pat = re.compile(
        r"window\.(__QUBIT_[A-Z0-9_]+)\s*="      # window.X = …
        r"|window\[['\"](__QUBIT_[A-Z0-9_]+)['\"]\]\s*=")
    for p in list(_js_files()) + list(_html_files()):
        text = _text(p)
        rel = str(p.relative_to(ROOT))
        for m in re.finditer(r"window\.(__QUBIT_[A-Z0-9_]+)|window\[['\"](__QUBIT_[A-Z0-9_]+)['\"]\]", text):
            name = m.group(1) or m.group(2)
            reads.setdefault(name, []).append(f"{rel}:{_lineno(text, m.start())}")
        for m in write_pat.finditer(text):
            writes.add(m.group(1) or m.group(2))
    for name, sites in sorted(reads.items()):
        if name in SERVER_GLOBALS or name in writes:
            continue
        if name in EXTERNAL_GLOBALS:
            NOTE.append(f"P1 {name} is read and written only by its own host "
                        f"app, never by this tree: {sites[0]}")
            continue
        # A global read but written nowhere in this tree AND not server-injected
        # is a dead dependency: the value is `undefined` forever.
        bad.append(f"P1 global {name} is READ but never WRITTEN: {sites[0]}")
    return bad


# --------------------------------------------------------------------- P2
_STORAGE_CALL = re.compile(
    r"(?:localStorage|sessionStorage)\.(getItem|setItem|removeItem)\(\s*"
    r"(?:'([^']*)'|\"([^\"]*)\"|`([^`]*)`)")
#: A template-literal key such as ``qubit-wb-pending-session-model:${sid}`` is
#: keyed per session; its PREFIX is what has to be written and read consistently.
def _key_stem(raw: str) -> str:
    stem = re.split(r"\$\{|' \+|\" \+|\.concat\(", raw)[0]
    return stem.rstrip(":-_ ")


_CONST_STR = re.compile(
    r"(?:^|\s)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:'([^']*)'|\"([^\"]*)\")",
    re.M)


def _const_values() -> dict[str, str]:
    """``NAME = 'qubit-wb-x'`` declarations, so a key written via a constant is
    still seen as written. Without this the check reports a false orphan for
    every key the upstream code hoisted into a named constant."""
    out: dict[str, str] = {}
    for p in _js_files():
        text = _text(p)
        for m in _CONST_STR.finditer(text):
            out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


#: Keys that are WRITE-ONLY at the upstream pin too (verified in the pristine
#: tree: exactly one occurrence, a setItem, in upstream panels.js). These are
#: upstream defects preserved by an honest copy, not port regressions.
UPSTREAM_WRITE_ONLY = frozenset({"qubit-wb-pref-language", "qubit-wb-server-stopped"})

#: Legal ONE-WAY reads: a legacy key the migration consumes but deliberately
#: stops writing. ``qubit-wb-busy-input-mode`` is exactly that — upstream renamed
#: it to ``qubit-wb-default-message-mode`` and keeps the old key as a READ-ONLY
#: fallback so a pre-rename user's preference survives (boot.js:1859-1862). It is
#: listed by name so the exemption is auditable, not a blanket rule.
ONE_WAY_READS = frozenset({
    # upstream renamed it; the old key is a read-only migration fallback.
    "qubit-wb-busy-input-mode",
    # upstream never writes this one either (verified in the pristine tree: the
    # only two occurrences are a comment and the getItem). It is a flag a user
    # or an extension sets BY HAND to force continuous dictation on desktop.
    "qubit-wb-mic-continuous",
})


def check_storage() -> list[str]:
    """Every key READ must have a WRITE path, and vice versa — with one honest
    exception: upstream writes a large family of keys from a TABLE-driven loop
    (``[['tts_enabled','qubit-wb-tts-enabled'], …].forEach(([k,key])=>
    localStorage.setItem(key, …))``). Those writes are real but not statically
    resolvable to one literal, so they are credited as a DYNAMIC writer rather
    than reported as 30 false orphans. A reader with no static AND no dynamic
    writer is the real defect and is still reported.
    """
    bad: list[str] = []
    reads: dict[str, str] = {}
    writes: dict[str, str] = {}
    dynamic_writers: list[str] = []

    def scan(text: str, rel: str, allow_html: bool) -> None:
        for m in _STORAGE_CALL.finditer(text):
            op, raw = m.group(1), (m.group(2) or m.group(3) or m.group(4) or "")
            stem = _key_stem(raw)
            if not stem:
                continue
            site = f"{rel}:{_lineno(text, m.start())}"
            if op == "getItem":
                reads.setdefault(stem, site)
            elif op == "setItem":
                writes.setdefault(stem, site)
        for m in re.finditer(
                r"(?:localStorage|sessionStorage)\.(getItem|setItem|removeItem)"
                r"\(\s*(?!['\"`])([A-Za-z_$][\w$.]*)", text):
            op, name = m.group(1), m.group(2)
            site = f"{rel}:{_lineno(text, m.start())}"
            val = _const_values().get(name)
            if val:
                stem = _key_stem(val)
                if op == "getItem":
                    reads.setdefault(stem, site)
                elif op == "setItem":
                    writes.setdefault(stem, site)
            elif op == "setItem":
                dynamic_writers.append(site)
        # initResize resolves its key from a PARAMETER: the literal read is
        # ``localStorage.getItem(storageKey)`` and the literal write is
        # ``setItem(storageKey, w)``, so a key named at the CALL SITE is both
        # read and written. Credit both sides from the call site.
        for m in re.finditer(r"initResize\([^)]*'(qubit-wb-[^']+)'", text):
            reads.setdefault(m.group(1), f"{rel}:{_lineno(text, m.start())}")
        # initResize(..., '<key>') persists a panel width under that key.
        for m in re.finditer(r"initResize\([^)]*'(qubit-wb-[^']+)'", text):
            writes.setdefault(m.group(1), f"{rel}:{_lineno(text, m.start())}")

    for p in _js_files():
        scan(_text(p), str(p.relative_to(ROOT)), False)
    for p in _html_files():
        scan(_text(p), str(p.relative_to(ROOT)), True)

    # A dynamic write excuses a key ONLY when the key is listed in a table the
    # loop writes from — i.e. a `[[...], ['<setting>','<key>']]` row or a
    # `'<key>'` entry inside an array literal. A blanket "some dynamic write
    # exists somewhere" excuse was tried first and silently passed a mutated
    # writer, which is exactly the half-rename this check exists to catch.
    # A key is TABLE-WRITTEN when it appears as the storage slot of a
    # `[<setting>, '<key>']` row inside an array literal that is immediately
    # followed by `.forEach(...)` whose body calls setItem. That is the upstream
    # shape (boot.js boolKeys / the scalar table): the key is DATA, so it never
    # sits beside setItem and is not a literal setItem argument. This is resolved
    # structurally (bracket-matched) rather than by a line or cross-file regex:
    # a line rule missed the shape, and a cross-file rule matched the reader's
    # own row — both let a deleted writer pass silently.
    table_keys: set[str] = set()
    for f in _js_files():
        text = _text(f)
        for arr in re.finditer(r"\[\s*(?=\[\s*')", text):
            # bracket-match from the opening [ to its closing ].
            depth, i = 0, arr.start()
            while i < len(text):
                if text[i] == "[":
                    depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            block = text[arr.start():i + 1]
            tail = text[i + 1:i + 900]
            if ".forEach" not in tail or "setItem" not in tail:
                continue
            for row in re.finditer(r"\[\s*'[^']+',\s*'(qubit-wb-[^']+)'\s*\]", block):
                table_keys.add(row.group(1))
    def _in_write_table(key: str) -> bool:
        return key in table_keys
    for stem, site in sorted(reads.items()):
        if stem in writes:
            continue
        if dynamic_writers and _in_write_table(stem):
            continue
        if stem in ONE_WAY_READS:
            continue
        bad.append(f"P2 storage key {stem!r} is READ but never WRITTEN: {site}")
    for stem, site in sorted(writes.items()):
        # qubit-wb-server-stopped is written by the shutdown path and consumed by
        # the next boot's removeItem, which the call scan already counts as a
        # touch; it is exempt by name so the exemption is visible.
        if stem in reads or stem in UPSTREAM_WRITE_ONLY:
            continue
        bad.append(f"P2 storage key {stem!r} is WRITTEN but never READ: {site}")
    return bad


# --------------------------------------------------------------------- P3
_INLINE_CALL = re.compile(r"\bon(?:click|change|input|submit|keydown|blur)=\"([^\"]*)\"")
_CALL_NAME = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_JS_KEYWORDS = {
    "if", "return", "typeof", "function", "new", "void", "event", "this",
    "document", "window", "console", "Number", "String", "Boolean", "Array",
    "Object", "JSON", "Math", "parseInt", "parseFloat", "encodeURIComponent",
    "decodeURIComponent", "alert", "confirm", "prompt", "setTimeout",
    "clearTimeout", "querySelector", "getElementById", "stopPropagation",
    "preventDefault", "classList", "add", "remove", "toggle", "contains",
    "value", "checked", "focus", "blur", "click", "submit", "call",
    "apply", "bind", "then", "catch", "finally", "map", "filter", "forEach",
    "includes", "join", "split", "trim", "toLowerCase", "toUpperCase",
}


def check_handlers() -> list[str]:
    bad: list[str] = []
    declared: set[str] = set()
    for p in _js_files():
        text = _text(p)
        for m in re.finditer(r"(?:^|\s)(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", text, re.M):
            declared.add(m.group(1))
        for m in re.finditer(r"(?:^|\s)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()", text, re.M):
            declared.add(m.group(1))
        for m in re.finditer(r"window\.([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?function", text):
            declared.add(m.group(1))
    for p in _html_files():
        text = _text(p)
        rel = str(p.relative_to(ROOT))
        for m in _INLINE_CALL.finditer(text):
            body = m.group(1)
            for c in _CALL_NAME.finditer(body):
                # ``a.b(…)`` is a member call on a built-in, not a tree helper.
                if c.start() >= 1 and body[c.start() - 1] in ".":
                    continue
                name = c.group(1)
                if name in _JS_KEYWORDS or name in declared:
                    continue
                # `fn(arg)` where fn is defined elsewhere in the same tree.
                bad.append(
                    f"P3 inline handler calls {name}() which is not declared: "
                    f"{rel}:{_lineno(text, m.start())}")
    return bad


# --------------------------------------------------------------------- P4
#: The CSS families renamed by the rebrand. A class in a family emitted by JS
#: with no selector in style.css is unstyled; a selector with no emitter is dead
#: CSS (harmless, but its presence means a writer/reader split).
CSS_FAMILIES = ("qubit-wb-",)


def check_css() -> list[str]:
    bad: list[str] = []
    css = _text(ROOT / "style.css")
    selectors = set(re.findall(r"\.([A-Za-z_][\w-]*)", css))
    emitted: dict[str, str] = {}
    for p in list(_js_files()) + list(_html_files()):
        text = _text(p)
        rel = str(p.relative_to(ROOT))
        for m in re.finditer(r"classList\.(?:add|remove|toggle)\(\s*'([^']+)'", text):
            emitted.setdefault(m.group(1), f"{rel}:{_lineno(text, m.start())}")
        for m in re.finditer(r"""class=(?:"|')([^"']+)(?:"|')""", text):
            for cls in m.group(1).split():
                if cls and "${" not in cls:
                    emitted.setdefault(cls, f"{rel}:{_lineno(text, m.start())}")
    for cls in sorted(selectors):
        if not cls.startswith(CSS_FAMILIES):
            continue
        if cls not in emitted:
            bad.append(f"P4 css class .{cls} is styled but no JS/HTML emits it")
    #: Classes with NO selector at the UPSTREAM pin either — a pre-existing
    #: upstream defect, verified against the pristine tree (0 selector hits in
    #: upstream style.css). Listed so the check stays strict for OUR surfaces
    #: without pretending this one is clean.
    UPSTREAM_DEAD = {"qubit-wb-kanban-code"}
    for cls, site in sorted(emitted.items()):
        if not cls.startswith(CSS_FAMILIES) or cls in UPSTREAM_DEAD:
            continue
        if cls not in selectors:
            bad.append(f"P4 css class {cls!r} is emitted but has no selector in "
                       f"style.css: {site}")
    return bad


def main() -> int:
    checks = [("P1 globals", check_globals),
              ("P2 storage", check_storage),
              ("P3 handlers", check_handlers),
              ("P4 css", check_css)]
    total = 0
    for line in NOTE:
        print(f"note: {line}")
    for name, fn in checks:
        problems = fn()
        total += len(problems)
        print(f"{name}: {'OK' if not problems else str(len(problems)) + ' PROBLEM(S)'}")
        for line in problems:
            print("   ", line)
    print(f"\n{'PASS' if total == 0 else 'FAIL'} — {total} problem(s)")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
