#!/usr/bin/env python3
"""P6-05 — deterministic structural accessibility audit for Qubit Office.

This is a **static** audit. It reads the office shell sources and the shell
stylesheet and checks the parts a machine can check without a browser:

* every interactive control in the shell-owned source has an accessible name;
* the shell has a skip link, a main landmark, a toolbar landmark and named
  dock regions;
* the canvas is not the only interface (semantic regions/lists/live regions);
* loading/error/status text is announced through a live region;
* no `outline: none` without a visible replacement;
* reduced-motion rules exist for both the media query and the shell attribute;
* shell controls meet WCAG 2.2 SC 2.5.8 (24x24 CSS px);
* status rules are never colour-only.

It deliberately does **not** claim an axe-core run: axe-core is not installed
in this checkout and the task runs offline. The report states
``axe: not_available`` and lists the manual reviews that were NOT performed.

Exit codes: 0 no error findings, 1 error findings, 2 usage/IO.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TASK_ID = "P6-05"
SCHEMA = "qubit-office-accessibility-audit/1"
REPO = Path(__file__).resolve().parents[1]
DEFAULT_PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
APP_SUBDIR = Path("game") / "app"
STYLE_SUBDIR = Path("game") / "styles"

#: Files this lane owns and may fix. A finding here is an ERROR (the audit must
#: fail the lane). Findings in other panels are reported as WARN because they
#: are owned by other lanes and booking them as hard failures here would block
#: this lane on work it must not edit.
OWNED_FILES = {"OfficeAppShell.tsx", "a11y.ts", "office-menu.ts"}
INTERACTIVE_TAGS = ("button", "a", "input", "select", "textarea")
MIN_TARGET_PX = 24
CREATE_RE = re.compile(
    r"\b(?:R|React)\.createElement\(\s*[\"'](" + "|".join(INTERACTIVE_TAGS) + r")[\"']",
)
NAME_ATTR_RE = re.compile(r"[\"']?(aria-label|aria-labelledby|title)[\"']?\s*:")
STRING_RE = re.compile(r"[\"'`]((?:[^\"'`\\]|\\.)*?[A-Za-z]{2,}(?:[^\"'`\\]|\\.)*?)[\"'`]")
RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
OUTLINE_NONE_RE = re.compile(r"outline\s*:\s*(?:none|0)(?:\s+[^;]*)?;", re.IGNORECASE)
OUTLINE_SET_RE = re.compile(r"outline\s*:\s*(?!\s*(?:none\b|0\b))[^;]+;", re.IGNORECASE)
STATE_KEY_RE = re.compile(r"\[\s*data-(?:state|status)\s*=\s*[\"'][^\"']+[\"']\s*\]")


@dataclass
class Finding:
    check: str
    severity: str  # "error" | "warn" | "info"
    file: str
    line: Optional[int]
    detail: str


def _finding(check: str, severity: str, file: str, line: Optional[int],
             detail: str) -> Finding:
    return Finding(check=check, severity=severity, file=file, line=line,
                   detail=detail)


def strip_ts_comments(source: str) -> str:
    """Remove block and line comments without touching string contents."""
    out: List[str] = []
    i = 0
    quote: Optional[str] = None
    while i < len(source):
        char = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if quote:
            out.append(char)
            if char == "\\":
                if i + 1 < len(source):
                    out.append(nxt)
                    i += 2
                    continue
            elif char == quote:
                quote = None
            i += 1
            continue
        if char in "\"'`":
            quote = char
            out.append(char)
            i += 1
            continue
        if char == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            if end == -1:
                break
            out.append("\n" * source.count("\n", i, end + 2))
            i = end + 2
            continue
        if char == "/" and nxt == "/":
            end = source.find("\n", i + 2)
            if end == -1:
                break
            i = end
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _match_delimiter(source: str, start: int, opening: str, closing: str) -> int:
    """Index of the matching `closing` for the `opening` at `start` (-1 miss)."""
    depth = 0
    quote: Optional[str] = None
    escaped = False
    i = start
    while i < len(source):
        char = source[i]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        else:
            if char in "\"'`":
                quote = char
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def iter_create_elements(source: str) -> Iterable[Tuple[str, int, str, str]]:
    """Yield (tag, start, props_source, children_source) for HTML factories.

    Only lowercase interactive tags are returned. The props object and the
    children are sliced with a delimiter matcher so nested objects, template
    literals and arrays do not truncate the block.
    """
    for match in CREATE_RE.finditer(source):
        tag = match.group(1)
        call_open = source.rfind("(", match.start(), match.end())
        call_close = _match_delimiter(source, call_open, "(", ")")
        if call_close == -1:
            continue
        cursor = match.end()
        while cursor < call_close and source[cursor] in " \t\r\n,":
            cursor += 1
        props = ""
        if cursor < call_close and source[cursor] == "{":
            props_close = _match_delimiter(source, cursor, "{", "}")
            if props_close == -1:
                continue
            props = source[cursor:props_close + 1]
            cursor = props_close + 1
        while cursor < call_close and source[cursor] in " \t\r\n,":
            cursor += 1
        children = source[cursor:call_close]
        yield tag, match.start(), props, children


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _wrapped_in_label(source: str, element_start: int) -> bool:
    """True when the element sits inside a `createElement("label", ...)` call."""
    for label in re.finditer(r"\b(?:R|React)\.createElement\(\s*[\"']label[\"']", source):
        call_open = source.rfind("(", label.start(), label.end())
        call_close = _match_delimiter(source, call_open, "(", ")")
        if call_close != -1 and call_open < element_start < call_close:
            return True
    return False


def _labelled_by_html_for(source: str, props: str) -> bool:
    id_match = re.search(r"\bid\s*:\s*[\"']([^\"']+)[\"']", props)
    if not id_match:
        return False
    identifier = re.escape(id_match.group(1))
    return bool(re.search(rf"htmlFor\s*:\s*[\"']{identifier}[\"']", source)
                or re.search(rf"htmlFor\s*=\s*[\"']{identifier}[\"']", source))



def _children_has_text(children: str) -> bool:
    """True when a string literal is used as text, not as a prop/tag value.

    A literal preceded by `:` is an object value and one preceded by `(` is the
    createElement tag, so `R.createElement("svg", { key: "i" }, null)` is not
    text. A literal after `,` or `[` is a rendered child.
    """
    for match in STRING_RE.finditer(children):
        cursor = match.start() - 1
        while cursor >= 0 and children[cursor] in " \t\r\n":
            cursor -= 1
        if cursor < 0 or children[cursor] not in ":(":
            return True
    return False


def _name_source(source: str, tag: str, start: int, props: str, children: str) -> Optional[str]:
    if NAME_ATTR_RE.search(props):
        return "attribute"
    if tag in ("input", "select", "textarea"):
        if _labelled_by_html_for(source, props):
            return "label[htmlFor]"
        if _wrapped_in_label(source, start):
            return "wrapping-label"
        return None
    if _children_has_text(children):
        return "text"
    # A single identifier child is a value that resolves to text (e.g.
    # `OFFICE_ART_CREDIT`, `text`, `label`); an element child is not.
    if re.fullmatch(r"\s*[A-Za-z_$][\w$.]*\s*", children or ""):
        return "identifier-text"
    # A spread fragment (e.g. `...common`) can carry the name; resolve only the
    # simplest local literal declarations.
    for spread in re.finditer(r"\.\.\.([A-Za-z_$][\w$]*)", children or ""):
        name = spread.group(1)
        definition = re.search(
            r"(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*[\[{]", source)
        if definition:
            bracket = "[" if source[definition.end() - 1] == "[" else "{"
            closing = "]" if bracket == "[" else "}"
            body_start = source.rfind(bracket, definition.start(), definition.end())
            body_end = _match_delimiter(source, body_start, bracket, closing)
            if body_end != -1 and STRING_RE.search(source[body_start:body_end]):
                return f"spread:{name}"
    for spread in re.finditer(r"\.\.\.([A-Za-z_$][\w$]*)", props):
        name = spread.group(1)
        definition = re.search(
            rf"(?:const|let|var)\s+{re.escape(name)}\s*=\s*\{{", source)
        if definition:
            body_start = source.find("{", definition.end() - 1)
            body_end = _match_delimiter(source, body_start, "{", "}")
            if body_end != -1 and NAME_ATTR_RE.search(source[body_start:body_end]):
                return f"spread:{name}"
    return None


def scan_controls(path: Path, source: str, owned: bool) -> List[Finding]:
    stripped = strip_ts_comments(source)
    findings: List[Finding] = []
    severity = "error" if owned else "warn"
    label = str(path)
    for tag, start, props, children in iter_create_elements(stripped):
        if _name_source(stripped, tag, start, props, children):
            continue
        findings.append(_finding(
            "interactive_name", severity, label, _line_of(stripped, start),
            f"<{tag}> has no accessible name (aria-label/aria-labelledby/title,"
            " a wrapping label, htmlFor, or text content)"
            + ("" if owned else " — panel owned by another lane"),
        ))
    return findings


def parse_css_rules(css: str) -> List[Tuple[str, str]]:
    return [(m.group(1).strip(), m.group(2)) for m in RULE_RE.finditer(css)]


def scan_css(css: str, label: str, *, primary: bool = True) -> List[Finding]:
    findings: List[Finding] = []

    # 1. outline: none must come with a visible replacement in the same rule.
    for selector, body in parse_css_rules(css):
        if not OUTLINE_NONE_RE.search(body):
            continue
        has_replacement = bool(
            OUTLINE_SET_RE.search(body)
            or re.search(r"box-shadow\s*:[^;]+;", body)
            or re.search(r"border(?:-color)?\s*:\s*(?!\s*(?:none\b|0\b))[^;]+;", body)
        )
        if not has_replacement:
            findings.append(_finding(
                "outline_replacement", "error", label, None,
                f"rule {selector!r} removes the outline with no visible replacement"))

    # 2. Reduced motion: the media query and the shell attribute must both exist.
    if primary and "prefers-reduced-motion: reduce" not in css:
        findings.append(_finding(
            "reduced_motion", "error", label, None,
            "no @media (prefers-reduced-motion: reduce) rule"))
    if primary and '[data-office-reduced-motion="1"]' not in css:
        findings.append(_finding(
            "reduced_motion", "error", label, None,
            "no [data-office-reduced-motion=\"1\"] rule"))

    # 3. WCAG 2.2 SC 2.5.8 — the shell control rule must state a 24px minimum
    #    in both dimensions.
    control_rule = ""
    if primary:
      for selector, body in parse_css_rules(css):
        if ".office-game__btn" in selector and "min-height" in body:
            control_rule = body
            break
      if not control_rule:
        findings.append(_finding(
            "target_size", "error", label, None,
            "no shell control rule states a minimum target size"))
      else:
        height = re.search(r"min-height\s*:\s*(\d+)px", control_rule)
        width = re.search(r"min-width\s*:\s*(\d+)px", control_rule)
        if not height or int(height.group(1)) < MIN_TARGET_PX:
            findings.append(_finding(
                "target_size", "error", label, None,
                f"shell control min-height is missing or below {MIN_TARGET_PX}px"))
        if not width or int(width.group(1)) < MIN_TARGET_PX:
            findings.append(_finding(
                "target_size", "error", label, None,
                f"shell control min-width is missing or below {MIN_TARGET_PX}px"))

    # 4. Status must never be colour-only. A rule keyed on data-state/status
    #    must set more than `color`.
    for selector, body in parse_css_rules(css):
        if not STATE_KEY_RE.search(selector):
            continue
        declarations = [
            decl.strip() for decl in body.split(";") if decl.strip()
        ]
        non_colour = [
            decl for decl in declarations
            if not re.match(r"^color\s*:", decl)
        ]
        if not non_colour:
            findings.append(_finding(
                "non_color_status", "error", label, None,
                f"status rule {selector!r} changes only the colour"))

    # 5. Focus visibility: a keyboard ring must be declared for shell controls.
    if primary and ".office-game__btn:focus-visible" not in css:
        findings.append(_finding(
            "focus_visible", "error", label, None,
            "no :focus-visible rule for the shell buttons"))
    if primary and ".office-game__skipLink" not in css:
        findings.append(_finding(
            "skip_link_style", "error", label, None,
            "skip link has no stylesheet rule"))
    return findings


def scan_shell_structure(source: str, label: str) -> List[Finding]:
    findings: List[Finding] = []
    checks = {
        "skip_link": (
            'href: "#office-game-main"' in source
            and "office-app-skip-link" in source),
        "skip_past_canvas": (
            'href: "#office-game-controls"' in source
            and "office-app-skip-controls" in source),
        "main_landmark": (
            'id: "office-game-main"' in source
            and '"main"' in source
            and "tabIndex: -1" in source),
        "toolbar_landmark": (
            'role: "toolbar"' in source
            and 'id: "office-game-controls"' in source),
        "live_region": (
            "office-app-announcer" in source
            and 'role: "status"' in source
            and '"aria-live": "polite"' in source),
        "escape_hatch": (
            "installEscapeToShell" in source
            and "office-game-controls" in source),
        "iframe_name": (
            '"data-testid": "office-app-frame"' in source
            and "title: OFFICE_BRAND.title" in source
            and '"aria-label": OFFICE_BRAND.name' in source),
        "no_application_role": 'role: "application"' not in source,
        "dock_regions": source.count('role: "region"') >= 6,
        "reduced_motion_attr": '"data-office-reduced-motion"' in source,
        "contrast_attr": '"data-office-contrast"' in source,
    }
    for check, passed in checks.items():
        if not passed:
            findings.append(_finding(
                "shell_structure", "error", label, None,
                f"missing shell accessibility wiring: {check}"))
    return findings


def scan_semantic_mirror(app_dir: Path) -> List[Finding]:
    findings: List[Finding] = []
    joined = ""
    for path in sorted(app_dir.glob("*.tsx")):
        joined += path.read_text(encoding="utf-8") + "\n"
    requirements = {
        "regions": joined.count('role: "region"') >= 5,
        "lists": ('createElement("ul"' in joined
                  or 'createElement("ol"' in joined
                  or "role: \"list\"" in joined),
        "sections": 'createElement("section"' in joined,
        "live_regions": joined.count("aria-live") >= 3,
        "headings": bool(re.search(r'createElement\("h[1-6]"', joined)),
    }
    for name, passed in requirements.items():
        if not passed:
            findings.append(_finding(
                "semantic_mirror", "error", str(app_dir), None,
                f"office app has no semantic DOM mirror for {name}"))
    return findings


def manual_checklist() -> List[Dict[str, Any]]:
    """What this machine audit cannot prove, stated as unperformed reviews."""
    return [
        {"item": "axe-core automated scan", "status": "not_performed",
         "reason": "axe-core is not installed and this task runs offline"},
        {"item": "keyboard-only traversal", "status": "unit_proven",
         "reason": "jsdom tests prove skip link, focus trap/restore and "
                   "Escape-to-shell; no real browser session was driven"},
        {"item": "NVDA / VoiceOver / TalkBack", "status": "not_performed",
         "reason": "no assistive technology was available on this host"},
        {"item": "high-contrast mode", "status": "not_performed",
         "reason": "token swap is implemented and detected; no OS high-contrast "
                   "session was exercised"},
        {"item": "200% zoom / reflow", "status": "not_performed",
         "reason": "CSS reflow rules exist; no browser zoom session was run"},
        {"item": "touch", "status": "not_performed",
         "reason": "target-size contract is pinned in CSS; no touch device was used"},
        {"item": "muted audio", "status": "not_performed",
         "reason": "critical status is textual by design; no muted-audio run"},
        {"item": "reduced motion", "status": "unit_proven",
         "reason": "media query and root attribute rules exist; no motion "
                   "capture was taken"},
        {"item": "screen-reader loading/error states", "status": "not_performed",
         "reason": "live regions exist and are unit-asserted; no screen reader "
                   "was run"},
    ]


def axe_status() -> Dict[str, Any]:
    return {
        "tool": "axe-core",
        "status": "not_available",
        "reason": "axe-core is not installed in this checkout and the task runs "
                  "without network access; no axe run was performed",
        "claimed_run": False,
    }


def build_report(plugin_root: Path) -> Dict[str, Any]:
    app_dir = plugin_root / APP_SUBDIR
    style_dir = plugin_root / STYLE_SUBDIR
    shell_path = app_dir / "OfficeAppShell.tsx"
    findings: List[Finding] = []
    errors = 0
    warnings = 0

    if not shell_path.is_file():
        findings.append(_finding(
            "io", "error", str(shell_path), None, "shell source is missing"))
        return _report(findings)

    for path in sorted(app_dir.glob("*.tsx")):
        owned = path.name in OWNED_FILES
        findings.extend(scan_controls(path, path.read_text(encoding="utf-8"), owned))
    findings.extend(scan_shell_structure(
        shell_path.read_text(encoding="utf-8"), str(shell_path)))
    findings.extend(scan_semantic_mirror(app_dir))
    for css_path in sorted(style_dir.glob("*.css")):
        findings.extend(scan_css(
            css_path.read_text(encoding="utf-8"), str(css_path),
            primary=css_path.name == "game.css"))
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings if item.severity == "warn")
    return _report(findings, errors, warnings, plugin_root)


def _report(findings: List[Finding], errors: Optional[int] = None,
            warnings: Optional[int] = None,
            plugin_root: Optional[Path] = None) -> Dict[str, Any]:
    if errors is None:
        errors = sum(1 for item in findings if item.severity == "error")
    if warnings is None:
        warnings = sum(1 for item in findings if item.severity == "warn")
    return {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "status": "PASS" if errors == 0 else "FAIL",
        "ok": errors == 0,
        "plugin_root": str(plugin_root) if plugin_root else None,
        "axe": axe_status(),
        "summary": {
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "checks": sorted({item.check for item in findings}) or ["all_passed"],
        },
        "findings": [asdict(item) for item in findings],
        "manual_review": manual_checklist(),
        "limitations": [
            "static source/CSS audit only; no browser, DOM layout or computed "
            "style was evaluated",
            "axe-core is not installed and no network is available, so no axe "
            "run was performed",
            "manual screen-reader (NVDA/VoiceOver/TalkBack), high-contrast, "
            "200% zoom, touch and muted-audio reviews were NOT performed",
            "controls in panels owned by other lanes are reported as warnings "
            "because this lane must not edit their component files",
            "the vendored Habblaud document is a separate origin; its internal "
            "controls are not audited here (owner ruling 16)",
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic structural accessibility audit (P6-05).")
    parser.add_argument("--json", action="store_true",
                        help="emit the full JSON report")
    parser.add_argument("--root", default=None,
                        help="plugin root (default: the Qubit Office plugin)")
    parser.add_argument("--out", default=None,
                        help="write the JSON report to this path")
    args = parser.parse_args(argv)

    plugin_root = Path(args.root).resolve() if args.root else DEFAULT_PLUGIN
    if not plugin_root.is_dir():
        print(f"error: plugin root not found: {plugin_root}", file=sys.stderr)
        return 2
    report = build_report(plugin_root)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        summary = report["summary"]
        print(f"{TASK_ID}: {report['status']} — "
              f"{summary['errors']} error(s), {summary['warnings']} warning(s)")
        for item in report["findings"]:
            location = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
            print(f"  [{item['severity']}] {item['check']} {location}: {item['detail']}")
        print(f"axe: {report['axe']['status']} — {report['axe']['reason']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
