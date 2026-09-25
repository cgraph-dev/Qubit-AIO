"""Task 14 — de-brand the remaining public-surface CLI leaks (v0.21.5 rebase).

The prep audit ``prep/TASK14_LEAKS.md`` listed 7 safe-to-fix public-surface
leaks (S1–S7) in the vendored ``qubit_runtime/qubit_cli`` tree. This test pins
each repaired site so the rebase cannot silently reintroduce it, keeps the
preserved wire/contract identifiers (``HERMES_*`` env vars, session-token
header), and re-runs the *widened* scanner ``cli`` surface — which now renders
``agent`` / ``auth`` / ``gateway`` / ``proxy`` / ``uninstall`` help, not just
``--help`` and ``update --help`` — asserting zero public leaks there.

A pure file-read assertion set is used for the repaired strings because the
leaks live in help/error/print literals that may not all be reachable in
isolation; the behavioural guard is the ``scan_cli`` case at the bottom.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "qubit_runtime" / "qubit_cli"
SCANNER = REPO / "scripts" / "qubit_public_surface_scan.py"

FILES = ("auth.py", "uninstall.py", "_parser.py", "auth_commands.py",
         "gateway.py", "proxy_cli.py")

#: The widened CLI help invocations the scanner must render.
EXPECTED_CLI_HELP_ARGS = (
    ("--help",),
    ("agent", "--help"),
    ("auth", "--help"),
    ("gateway", "--help"),
    ("proxy", "--help"),
    ("uninstall", "--help"),
    ("update", "--help"),
)


def _read(name: str) -> str:
    return (CLI / name).read_text(encoding="utf-8")


def _load_scanner():
    name = "qubit_public_surface_scan_task14"
    spec = importlib.util.spec_from_file_location(name, SCANNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scanner():
    assert SCANNER.is_file(), "scripts/qubit_public_surface_scan.py is missing"
    return _load_scanner()


# --------------------------------------------------------------- S1 auth.py
def test_auth_no_provider_error_points_at_qubit_home():
    src = _read("auth.py")
    # The old runtime AuthError ended ``... in ~/.hermes/.env.``; comments /
    # docstrings may still name the reference home as an internal seam.
    assert "in ~/.hermes/.env." not in src
    assert "display_qubit_home()" in src


# ---------------------------------------------------------- S2 uninstall.py
def test_uninstall_help_points_at_qubit_cli():
    src = _read("uninstall.py")
    assert "Run 'hermes' to use the CLI" not in src
    assert "Run 'qubit' to use the CLI" in src


# ------------------------------------------------- S3 + S7 _parser.py
def test_parser_examples_and_prog_are_qubit():
    src = _read("_parser.py")
    assert "hermes-agent-dev" not in src
    assert "qubit-agent-dev" in src
    assert 'prog="hermes"' not in src
    assert 'prog="qubit"' in src


# ----------------------------------------------------- S4 auth_commands.py
def test_auth_commands_does_not_advertise_reference_command():
    src = _read("auth_commands.py")
    assert "Reference behavior" not in src
    assert "hermes auth upgrade" not in src
    assert "qubit auth upgrade" in src


# --------------------------------------------------------- S5 gateway.py
def test_gateway_help_does_not_point_at_external_home():
    src = _read("gateway.py")
    assert "account_id/token in ~/.hermes/.env" not in src
    assert "Set these env vars in ~/.hermes/.env" not in src


# -------------------------------------------------------- S6 proxy_cli.py
def test_proxy_cli_points_at_qubit_env():
    src = _read("proxy_cli.py")
    assert "~/.hermes/.env for discovery" not in src
    assert "display_qubit_home()" in src
    assert "for discovery" in src


# --------------------------------------------------------- preserved wire ids
def test_wire_identifiers_and_legal_provenance_preserved():
    # HERMES_* env names are load-bearing wire/config identifiers — never
    # renamed by this de-brand pass.
    parser = _read("_parser.py")
    assert "HERMES_INFERENCE_MODEL" in parser
    assert "HERMES_ACCEPT_HOOKS" in parser
    # Legal attribution is untouched (not among the edited files).
    assert (REPO / "LICENSE").is_file()
    assert (REPO / "THIRD_PARTY_NOTICES.md").is_file()


# ------------------------------------------------- scanner widening guarantees
def test_scanner_cli_help_args_are_widened(scanner):
    rendered = {tuple(argv) for argv in scanner.CLI_HELP_ARGS}
    for expected in EXPECTED_CLI_HELP_ARGS:
        assert expected in rendered, f"scanner must render {expected}"


def test_scanner_wire_regex_preserves_ids_flags_product_copy(scanner):
    rx = scanner.CLI_WIRE_IDENTIFIER_RE
    assert rx.search("HERMES_INFERENCE_MODEL")
    assert rx.search("X-Hermes-Session-Token")
    assert not rx.search("hermes-agent-dev")
    assert not rx.search("~/.hermes/.env")
    assert not rx.search("Run 'hermes' to use the CLI")


def test_widened_cli_surface_has_zero_public_leaks(scanner, tmp_path,
                                                   monkeypatch):
    home = tmp_path / "home"
    qubit_home = tmp_path / "qubit-home"
    for value in (home, qubit_home, tmp_path / "xdg-config",
                  tmp_path / "xdg-state", tmp_path / "xdg-data",
                  tmp_path / "xdg-cache"):
        value.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(qubit_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    findings, allow = scanner.scan_cli()
    assert not findings, "\n".join(
        f"[{f['classification']}] {f['file']} token={f['token']!r} "
        f":: {f['text']}" for f in findings)
    # The scan must have actually rendered the preserved wire identifiers.
    assert any(key.endswith(":wire_identifier:hermes") for key in allow), allow
