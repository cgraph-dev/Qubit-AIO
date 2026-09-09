"""Packet 8T-F — Hermes import-agent parity via quarantined inspection.

Proves foreign trees are inventoried structurally (bounded, redacted,
never executed/stored), symlinks and oversized files are contained, and
content import is refused (reviewed-code policy; credentials never move).
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path


def _tree(tmp_path, kind="claude-code"):
    root = tmp_path / "foreign"
    root.mkdir(exist_ok=True)
    if kind == "claude-code":
        (root / "CLAUDE.md").write_text("# Rules\nBe nice\n")
        (root / "settings.json").write_text(json.dumps({"model": "x"}))
        (root / "notes.txt").write_text("not a known file")
    else:
        (root / "AGENTS.md").write_text("# Agents\n")
        (root / "config.json").write_text(json.dumps(["mcp", 1]))
    return root


def test_inspect_structural_only(tmp_path):
    from voicebridge import import_agent as IA

    out = IA.inspect_tree(str(_tree(tmp_path)))
    assert out["state"] == "OK"
    assert out["detected_kind"] == "claude-code"
    names = [f["file"] for f in out["files"]]
    assert "CLAUDE.md" in names and "settings.json" in names
    assert "notes.txt" not in names, "only well-known files open"
    md = next(f for f in out["files"] if f["file"] == "CLAUDE.md")
    assert md["lines"] == 2 and "Be nice" not in json.dumps(out), \
        "metadata only, never content"
    js = next(f for f in out["files"] if f["file"] == "settings.json")
    assert js["top_level_keys"] == ["model"]


def test_inspect_codex_and_kind_mismatch(tmp_path):
    from voicebridge import import_agent as IA

    out = IA.inspect_tree(str(_tree(tmp_path, "codex")))
    assert out["detected_kind"] == "codex"
    try:
        IA.inspect_tree(str(_tree(tmp_path, "codex")), "claude-code")
    except IA.ImportAgentError as exc:
        assert exc.code == "IMPORT_AGENT_BAD_KIND"
    else:
        raise AssertionError("kind mismatch must fail")


def test_inspect_containment(tmp_path):
    import os

    from voicebridge import import_agent as IA

    root = _tree(tmp_path)
    claude = root / "CLAUDE.md"
    claude.write_text("x")
    os.truncate(claude, IA.MAX_FILE_BYTES + 8)
    link = root / "settings.json"
    link.unlink()
    try:
        link.symlink_to("/etc/hostname")
        linked = True
    except OSError:
        linked = False
    out = IA.inspect_tree(str(root))
    by_name = {f["file"]: f for f in out["files"]}
    assert by_name["CLAUDE.md"]["state"] == "TOO_LARGE", out
    if linked:
        assert "settings.json" not in by_name, "symlinks never followed"
    assert "notes.txt" not in by_name


def test_inspect_bad_source_and_default(tmp_path, monkeypatch):
    from voicebridge import import_agent as IA

    try:
        IA.inspect_tree(str(tmp_path / "missing"))
    except IA.ImportAgentError as exc:
        assert exc.code == "IMPORT_AGENT_BAD_SOURCE"
    else:
        raise AssertionError("missing source must fail")
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        IA._default_source()
    except IA.ImportAgentError as exc:
        assert exc.code == "IMPORT_AGENT_BAD_SOURCE"
    else:
        raise AssertionError("empty home must not detect")


def test_import_refused_and_redacted():
    from voicebridge import import_agent as IA

    out = IA.refuse_import()
    assert out["code"] == "IMPORT_AGENT_REVIEW_REQUIRED"
    assert "Credentials are never imported" in out["explanation"] or \
        "credentials are never imported" in out["explanation"].lower()


def test_cli_wiring(tmp_path):
    from voicebridge import cli as CLI

    root = _tree(tmp_path)

    class _A:
        def __init__(self, cmd, **kw):
            self.import_agent_command = cmd
            self.__dict__.update(kw)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_import_agent(None, _A(
            "inspect", source=str(root), kind="")) == 0
    out = json.loads(buf.getvalue())
    assert out["detected_kind"] == "claude-code"
    assert "Be nice" not in buf.getvalue()
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_import_agent(None, _A("import")) == 2
    assert json.loads(buf.getvalue())["code"] == "IMPORT_AGENT_REVIEW_REQUIRED"
    assert "import-agent" in CLI.build_parser().format_help()


def test_capability_catalog_reports_import_agent_implemented():
    from voicebridge.capability_catalog import check

    out = check("import-agent")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
