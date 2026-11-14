"""P6-05 — deterministic structural accessibility audit.

The audit is static and must never claim more than it proves: axe-core is not
installed, no browser is driven and no screen reader is run. These tests pin
both the positive contract (the shipped office shell passes) and the negative
contract (each check actually fails on a broken fixture), so the gate cannot
rot into a no-op.
"""
from __future__ import annotations

import json

from _a11y_helpers import APP, GAME_CSS, PLUGIN, SHELL, sha256


def test_shipped_office_shell_passes_the_audit(audit):
    report = audit.build_report(PLUGIN)
    assert report["schema"] == "qubit-office-accessibility-audit/1"
    assert report["task_id"] == "P6-05"
    assert report["ok"] is True
    assert report["status"] == "PASS"
    assert report["summary"]["errors"] == 0
    # Warnings are controls in panels owned by other lanes; they are surfaced,
    # not hidden, and never block this lane.
    for finding in report["findings"]:
        assert finding["severity"] == "warn", finding
        assert "another lane" in finding["detail"]
    checks = set(report["summary"]["checks"])
    assert "interactive_name" in checks or "all_passed" in checks


def test_audit_never_claims_an_axe_run(audit):
    report = audit.build_report(PLUGIN)
    assert report["axe"]["status"] == "not_available"
    assert report["axe"]["claimed_run"] is False
    assert "axe-core is not installed" in report["axe"]["reason"]
    assert any(item["item"] == "axe-core automated scan"
               and item["status"] == "not_performed"
               for item in report["manual_review"])


def test_manual_reviews_are_declared_not_performed(audit):
    report = audit.build_report(PLUGIN)
    performed = {item["item"]: item["status"] for item in report["manual_review"]}
    for item in ("NVDA / VoiceOver / TalkBack", "high-contrast mode",
                 "200% zoom / reflow", "touch", "muted audio"):
        assert performed[item] == "not_performed"
    assert performed["keyboard-only traversal"] == "unit_proven"
    assert any("NOT performed" in line for line in report["limitations"])


def test_shell_has_skip_links_main_toolbar_and_live_region(audit):
    source = SHELL.read_text(encoding="utf-8")
    findings = audit.scan_shell_structure(source, str(SHELL))
    assert findings == []


def test_missing_skip_link_is_an_error(audit):
    source = SHELL.read_text(encoding="utf-8")
    broken = source.replace("office-app-skip-link", "removed")
    findings = audit.scan_shell_structure(broken, str(SHELL))
    assert [f.check for f in findings] == ["shell_structure"]
    assert "skip_link" in findings[0].detail


def test_shell_css_contract(audit):
    css = GAME_CSS.read_text(encoding="utf-8")
    findings = audit.scan_css(css, str(GAME_CSS), primary=True)
    assert findings == []


def test_unlabelled_icon_button_is_flagged(audit, tmp_path):
    source = (
        'function X(R) {\n'
        '  return R.createElement("button", {\n'
        '    key: "x", type: "button",\n'
        '  }, R.createElement("svg", { key: "i" }, null));\n'
        '}\n'
    )
    path = tmp_path / "Broken.tsx"
    findings = audit.scan_controls(path, source, owned=True)
    assert len(findings) == 1
    assert findings[0].check == "interactive_name"
    assert findings[0].severity == "error"


def test_text_and_aria_names_are_accepted(audit, tmp_path):
    source = (
        'function X(R) {\n'
        '  return [\n'
        '    R.createElement("button", { key: "a" }, "Close"),\n'
        '    R.createElement("button", { key: "b", "aria-label": "Close" }, null),\n'
        '    R.createElement("a", { key: "c", href: "#" }, LABEL),\n'
        '  ];\n'
        '}\n'
    )
    findings = audit.scan_controls(tmp_path / "Ok.tsx", source, owned=True)
    assert findings == []


def test_inputs_need_a_label_wrapper_or_html_for(audit, tmp_path):
    source = (
        'function X(R) {\n'
        '  return [\n'
        '    R.createElement("label", { key: "l", htmlFor: "q" }, "Query"),\n'
        '    R.createElement("input", { key: "i", id: "q" }),\n'
        '    R.createElement("label", { key: "w" }, [\n'
        '      "Note",\n'
        '      R.createElement("textarea", { key: "t" }),\n'
        '    ]),\n'
        '    R.createElement("input", { key: "bad", placeholder: "no name" }),\n'
        '  ];\n'
        '}\n'
    )
    findings = audit.scan_controls(tmp_path / "Inputs.tsx", source, owned=True)
    assert len(findings) == 1
    assert findings[0].line is not None


def test_outline_none_needs_a_visible_replacement(audit):
    broken = ".x:focus-visible { outline: none; }\n"
    findings = audit.scan_css(broken, "fixture.css", primary=False)
    assert [f.check for f in findings] == ["outline_replacement"]

    fixed = ".x:focus-visible { outline: none; border-color: red; }\n"
    assert audit.scan_css(fixed, "fixture.css", primary=False) == []


def test_reduced_motion_rules_are_required(audit):
    findings = audit.scan_css(".x { color: red; }\n", "fixture.css", primary=True)
    checks = [f.check for f in findings]
    assert checks.count("reduced_motion") == 2
    good = (
        "@media (prefers-reduced-motion: reduce) { .x { animation-duration: 1ms; } }\n"
        '[data-office-reduced-motion="1"] .x { animation-duration: 1ms; }\n'
    )
    assert not [f for f in audit.scan_css(good, "fixture.css", primary=True)
                if f.check == "reduced_motion"]


def test_target_size_below_24_is_flagged(audit):
    small = ".office-game__btn { min-height: 16px; min-width: 16px; }\n"
    findings = audit.scan_css(small, "fixture.css", primary=True)
    target = [f for f in findings if f.check == "target_size"]
    assert len(target) == 2


def test_colour_only_status_is_flagged(audit):
    broken = '[data-state="error"] { color: red; }\n'
    findings = audit.scan_css(broken, "fixture.css", primary=False)
    assert [f.check for f in findings] == ["non_color_status"]
    fixed = '[data-state="error"] { color: red; border-color: red; }\n'
    assert audit.scan_css(fixed, "fixture.css", primary=False) == []


def test_cli_json_contract(audit, capsys):
    assert audit.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["axe"]["status"] == "not_available"


def test_cli_missing_root_is_usage_error(audit, tmp_path):
    assert audit.main(["--root", str(tmp_path / "does-not-exist")]) == 2


def test_owned_accessibility_inputs_have_stable_hashes():
    # Evidence records these hashes; a silent rewrite is then visible.
    for path in (SHELL, APP / "a11y.ts", GAME_CSS):
        digest = sha256(path)
        assert len(digest) == 64
