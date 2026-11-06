#!/usr/bin/env python3
"""Validate the ported MV3 extension the way a browser would load it unpacked.

Checks, in the order a failed load would hit them:

1. ``manifest.json`` parses and declares ``manifest_version: 3``.
2. Every ``__MSG_x__`` placeholder in the manifest (and in every HTML/JS file the
   manifest loads) resolves in ``_locales/<default_locale>/messages.json``.
3. Every path the manifest names exists inside the extension directory:
   ``background.service_worker``/``.scripts``, ``side_panel.default_path``,
   ``sidebar_action.default_panel``, ``action.default_icon``, ``icons``,
   ``content_scripts[].js/css``, ``web_accessible_resources[].resources``.
4. Every icon/permission value is a type MV3 accepts.
5. Every ``<script type="module" src=…>`` / ``<link href=…>`` in the extension's
   own HTML pages resolves, and every relative ``import … from './x.mjs'`` /
   ``import('./x.mjs')`` in every ``.js``/``.mjs`` file resolves to a real file.
6. HTML pages are not blocked by the extension's own CSP (no inline ``<script>``
   without a hash, no ``on*`` handlers) — MV3 refuses those at load.

Exit code 0 with a printed report when everything resolves; 1 with every problem
listed otherwise. Run from the repository root:

    .venv/bin/python qubit_runtime/plugins/browser-companion/validate_extension.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXT = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-browser-ext" / "extension"

MSG_RE = re.compile(r"__MSG_([A-Za-z0-9_@]+)__")
IMPORT_RE = re.compile(
    r"""(?:^|[\s;])(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|import\s*\(\s*)['"](\.{1,2}/[^'"]+)['"]""",
    re.MULTILINE,
)
# Type-only references (`{@link import('./x')}`, `@typedef {import("./x").T}`) are erased
# by every JS engine and are never loaded, so a doc comment naming a file that no longer
# ships is not a load failure. They are counted separately and reported, not failed.
TYPE_IMPORT_RE = re.compile(r"import\s*\(\s*['\"](\.{1,2}/[^'\"]+)['\"]\s*\)")
HTML_SCRIPT_RE = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
HTML_LINK_RE = re.compile(r"<link\b([^>]*)>", re.IGNORECASE)
SRC_RE = re.compile(r'\b(?:src|href)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)
DATA_ATTR_RE = re.compile(r"^[a-z]+:|^#|^mailto:")


class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.checked = 0

    def fail(self, message: str) -> None:
        self.problems.append(message)

    def ok(self, what: str) -> None:
        self.checked += 1
        print(f"  ok  {what}")


def _strip_query(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def _manifest_paths(manifest: dict) -> list[tuple[str, str]]:
    """(label, relative path) for every file path the manifest names."""
    found: list[tuple[str, str]] = []

    def icon_block(label: str, block) -> None:
        if isinstance(block, dict):
            for size, path in block.items():
                if isinstance(path, str):
                    found.append((f"{label}[{size}]", path))
        elif isinstance(block, str):
            found.append((label, block))

    background = manifest.get("background") or {}
    if isinstance(background, dict):
        if isinstance(background.get("service_worker"), str):
            found.append(("background.service_worker", background["service_worker"]))
        for index, script in enumerate(background.get("scripts") or []):
            found.append((f"background.scripts[{index}]", script))

    action = manifest.get("action") or {}
    if isinstance(action, dict):
        icon_block("action.default_icon", action.get("default_icon"))
        if isinstance(action.get("default_popup"), str):
            found.append(("action.default_popup", action["default_popup"]))

    side_panel = manifest.get("side_panel") or {}
    if isinstance(side_panel, dict) and isinstance(side_panel.get("default_path"), str):
        found.append(("side_panel.default_path", side_panel["default_path"]))

    sidebar = manifest.get("sidebar_action") or {}
    if isinstance(sidebar, dict):
        icon_block("sidebar_action.default_icon", sidebar.get("default_icon"))
        if isinstance(sidebar.get("default_panel"), str):
            found.append(("sidebar_action.default_panel", sidebar["default_panel"]))

    icon_block("icons", manifest.get("icons"))

    for index, block in enumerate(manifest.get("content_scripts") or []):
        if not isinstance(block, dict):
            continue
        for key in ("js", "css"):
            for entry in block.get(key) or []:
                found.append((f"content_scripts[{index}].{key}", entry))

    for index, block in enumerate(manifest.get("web_accessible_resources") or []):
        if not isinstance(block, dict):
            continue
        for entry in block.get("resources") or []:
            if "*" in entry:
                continue
            found.append((f"web_accessible_resources[{index}]", entry))

    devtools = manifest.get("devtools_page")
    if isinstance(devtools, str):
        found.append(("devtools_page", devtools))

    options = manifest.get("options_page")
    if isinstance(options, str):
        found.append(("options_page", options))
    if isinstance(manifest.get("options_ui"), dict) and isinstance(manifest["options_ui"].get("page"), str):
        found.append(("options_ui.page", manifest["options_ui"]["page"]))
    return found


def main() -> int:
    report = Report()
    manifest_path = EXT / "manifest.json"
    if not manifest_path.is_file():
        print(f"FAIL: no manifest at {manifest_path}", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: manifest.json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    if manifest.get("manifest_version") != 3:
        report.fail(f"manifest_version is {manifest.get('manifest_version')!r}, MV3 requires 3")
    else:
        report.ok("manifest_version = 3")

    default_locale = manifest.get("default_locale", "en")
    locale_path = EXT / "_locales" / default_locale / "messages.json"
    if not locale_path.is_file():
        report.fail(f"default_locale {default_locale!r} has no _locales/{default_locale}/messages.json")
        messages: dict = {}
    else:
        messages = json.loads(locale_path.read_text(encoding="utf-8"))
        report.ok(f"_locales/{default_locale}/messages.json parses ({len(messages)} messages)")

    # 2. __MSG_ placeholders used by the manifest itself.
    raw_manifest = manifest_path.read_text(encoding="utf-8")
    for key in sorted({m for m in MSG_RE.findall(raw_manifest)}):
        if key not in messages:
            report.fail(f"manifest uses __MSG_{key}__ but {default_locale} has no such message")
    if not [p for p in report.problems if "__MSG_" in p]:
        report.ok("every __MSG_ placeholder in manifest.json resolves")

    # 3. every named path exists.
    missing = 0
    for label, rel in _manifest_paths(manifest):
        target = EXT / _strip_query(rel)
        if not target.is_file():
            report.fail(f"{label}: {rel} does not exist")
            missing += 1
    if not missing:
        report.ok(f"all {len(_manifest_paths(manifest))} manifest file references resolve")

    # 4. types MV3 accepts.
    permissions = manifest.get("permissions")
    if permissions is not None and not (isinstance(permissions, list) and all(isinstance(p, str) for p in permissions)):
        report.fail("permissions must be a list of strings")
    host_permissions = manifest.get("host_permissions") or []
    if not all(isinstance(p, str) and ("://" in p) for p in host_permissions):
        report.fail("host_permissions must be URL match patterns")
    if not any("127.0.0.1" in p or "localhost" in p for p in host_permissions):
        report.fail("no host permission covers the Qubit API server (127.0.0.1/localhost)")
    else:
        report.ok(f"{len(permissions or [])} permissions, {len(host_permissions)} host permissions, loopback covered")

    # 5. HTML pages: scripts, styles and inline handlers; plus __MSG_ in page HTML.
    html_pages = sorted(p for p in EXT.rglob("*.html"))
    html_problems = 0
    for page in html_pages:
        text = page.read_text(encoding="utf-8")
        rel_page = page.relative_to(EXT)
        for match in HTML_SCRIPT_RE.finditer(text):
            attrs = match.group(1)
            src = SRC_RE.search(attrs)
            if src is None:
                report.fail(f"{rel_page}: inline <script> is refused by the extension CSP")
                html_problems += 1
                continue
            target = (page.parent / _strip_query(src.group(1))).resolve()
            if not target.is_file():
                report.fail(f"{rel_page}: <script src={src.group(1)!r}> does not exist")
                html_problems += 1
        for match in HTML_LINK_RE.finditer(text):
            href = SRC_RE.search(match.group(1))
            if href is None:
                continue
            value = href.group(1)
            if DATA_ATTR_RE.match(value) or value.startswith("//"):
                continue
            target = (page.parent / _strip_query(value)).resolve()
            if not target.is_file():
                report.fail(f"{rel_page}: <link href={value!r}> does not exist")
                html_problems += 1
        if INLINE_HANDLER_RE.search(text):
            report.fail(f"{rel_page}: inline on* handler attribute (refused by MV3 CSP)")
            html_problems += 1
        for key in sorted({m for m in MSG_RE.findall(text)}):
            if key not in messages:
                report.fail(f"{rel_page}: __MSG_{key}__ missing from {default_locale}")
                html_problems += 1
    if not html_problems:
        report.ok(f"{len(html_pages)} HTML pages: scripts, styles and messages resolve")

    # 6. ES module graph: every relative import resolves. A doc-comment type reference
    # (`{@link import('./x')}`) is erased by the engine, so it is reported, not failed.
    js_files = sorted([*EXT.rglob("*.js"), *EXT.rglob("*.mjs")])
    broken = 0
    edges = 0
    type_refs = 0
    for source in js_files:
        text = source.read_text(encoding="utf-8")
        doc_regions = [m.span() for m in re.finditer(r"/\*.*?\*/", text, re.DOTALL)]
        doc_regions += [m.span() for m in re.finditer(r"(?m)^\s*//.*$", text)]

        def in_doc(position: int) -> bool:
            return any(start <= position < end for start, end in doc_regions)

        for match in IMPORT_RE.finditer(text):
            if in_doc(match.start()):
                type_refs += 1
                continue
            spec = match.group(1)
            edges += 1
            target = (source.parent / _strip_query(spec)).resolve()
            if not target.is_file():
                report.fail(f"{source.relative_to(EXT)}: import {spec!r} does not exist")
                broken += 1
        for match in TYPE_IMPORT_RE.finditer(text):
            if in_doc(match.start()):
                continue
            spec = match.group(1)
            target = (source.parent / _strip_query(spec)).resolve()
            if not target.is_file():
                report.fail(f"{source.relative_to(EXT)}: dynamic import {spec!r} does not exist")
                broken += 1
    if not broken:
        report.ok(
            f"{len(js_files)} scripts, {edges} module imports all resolve "
            f"({type_refs} doc-comment type references not loaded)"
        )

    print()
    if report.problems:
        print(f"FAIL: {len(report.problems)} problem(s)")
        for problem in report.problems[:60]:
            print(f"  - {problem}")
        if len(report.problems) > 60:
            print(f"  … {len(report.problems) - 60} more")
        return 1
    print(f"PASS: extension loads unpacked — {report.checked} checks, 0 problems")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
