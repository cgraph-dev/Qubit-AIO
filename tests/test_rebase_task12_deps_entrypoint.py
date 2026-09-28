"""Task 12 (v0.21.5 rebase) — dependency/packaging reconciliation + ``qubit-agent`` argv.

Focused, hermetic regression for the rebase follow-up:

* the vendored distribution keeps its Qubit identity (``qubit-agent``) with the
  pin-matching version (``0.21.5``) and Qubit console scripts;
* the run-needed core deps the rebase introduced (``truststore``, ``tomli-w``,
  ``pillow-heif``, ``resvg-py``) are present both in the vendored manifest and in
  the product manifest a clean install actually resolves;
* the product mirrors the vendored security/platform pins (``cryptography``
  50.0.1, ``ruamel.yaml`` 0.18.16, ``uvicorn`` without the ``[standard]`` extra
  that Android/Termux cannot build);
* the ported ``agent/legacy_cli.py`` entry point honors argv
  (``--help``/``--version``/``--query``/``--list-tools``) instead of silently
  dropping it like ``run_agent:main`` did (#54648).

No network, no model, no venv: the vendored module is loaded in-process and the
runner is stubbed. HOME/QUBIT_HOME are throwaway when the suite is invoked.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"

VENDORED_PYPROJECT = RUNTIME / "pyproject.toml"
PRODUCT_PYPROJECT = REPO / "pyproject.toml"
LEGACY_CLI = RUNTIME / "agent" / "legacy_cli.py"
CLI_INIT = RUNTIME / "qubit_cli" / "__init__.py"

# The pin's release date (v0.21.5, 69948c0057) as declared by qubit_cli.
PIN_RELEASE_DATE = "2026.9.24"
PIN_VERSION = "0.21.5"

# Deps the rebase added to the vendored core that a runtime turn actually needs.
RUN_NEEDED_DEPS = ("truststore", "tomli-w", "pillow-heif", "resvg-py")


def _load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _normalized(dep: str) -> str:
    """Lowercased, underscore-normalized requirement name without version/extras/markers."""
    name = dep.split(";")[0].strip()
    name = re.sub(r"\[.*?\]", "", name)
    name = re.split(r"[<>=!~ ]", name, maxsplit=1)[0]
    return name.lower().replace("_", "-").strip()


def _dep_map(path: Path) -> dict[str, str]:
    return {_normalized(d): d for d in _load_toml(path)["project"]["dependencies"]}


def _load_legacy_cli():
    sys.path.insert(0, str(RUNTIME))
    try:
        import agent.legacy_cli as legacy_cli  # type: ignore[import-not-found]
        return legacy_cli
    finally:
        sys.path.pop(0)


# --------------------------------------------------------------- vendored identity
def test_vendored_distribution_identity_is_qubit_and_pin_versioned():
    project = _load_toml(VENDORED_PYPROJECT)["project"]
    assert project["name"] == "qubit-agent"
    assert project["version"] == PIN_VERSION
    scripts = project["scripts"]
    assert scripts["qubit"] == "qubit_cli.main:main"
    assert scripts["qubit-agent"] == "agent.legacy_cli:main"
    assert scripts["qubit-acp"] == "acp_adapter.entry:main"
    assert not [k for k in scripts if "hermes" in k.lower()], scripts


def test_vendored_requires_python_adopts_the_upstream_policy():
    # The v0.21.1 local cap (<3.14) is gone: the rebase adopts upstream's range.
    rp = _load_toml(VENDORED_PYPROJECT)["project"]["requires-python"]
    assert rp == ">=3.11,<3.15", rp


def test_qubit_cli_release_date_matches_the_pin():
    source = CLI_INIT.read_text(encoding="utf-8")
    match = re.search(r'__release_date__\s*=\s*"([^"]+)"', source)
    assert match, "qubit_cli.__release_date__ must be declared"
    assert match.group(1) == PIN_RELEASE_DATE


# --------------------------------------------------------------- dependency parity
def test_run_needed_deps_present_in_both_manifests():
    vendored = _dep_map(VENDORED_PYPROJECT)
    product = _dep_map(PRODUCT_PYPROJECT)
    for name in RUN_NEEDED_DEPS:
        assert name in vendored, f"{name} missing from vendored deps"
        assert name in product, f"{name} missing from product deps"


def test_product_mirrors_vendored_security_and_platform_pins():
    product = _dep_map(PRODUCT_PYPROJECT)
    assert product["cryptography"] == "cryptography==50.0.1"
    assert product["ruamel.yaml"] == "ruamel.yaml==0.18.16"
    assert "httptools" in product and "watchfiles" in product
    raw = PRODUCT_PYPROJECT.read_text(encoding="utf-8")
    assert "uvicorn[standard]" not in raw, "Android/Termux cannot build uvloop"
    # The product resolves on 3.12+ (PEP 701 f-strings; installer-provisioned),
    # and its declared window stays inside the vendored runtime's upstream-
    # aligned range (`>=3.11,<3.15`): the fork is force-included, so the product
    # advertisement must not exceed it.
    product_rp = _load_toml(PRODUCT_PYPROJECT)["project"]["requires-python"]
    vendored_rp = _load_toml(VENDORED_PYPROJECT)["project"]["requires-python"]
    assert product_rp.startswith(">=3.12")
    assert product_rp == ">=3.12,<3.15", product_rp
    assert vendored_rp == ">=3.11,<3.15", vendored_rp


# --------------------------------------------------------------- argv entry point
def test_legacy_cli_help_and_version(capsys):
    legacy_cli = _load_legacy_cli()
    assert legacy_cli._build_parser().prog == "qubit-agent"

    with pytest.raises(SystemExit) as excinfo:
        legacy_cli.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage: qubit-agent" in out
    assert "--query" in out and "--list-tools" in out

    with pytest.raises(SystemExit) as excinfo:
        legacy_cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "Qubit Agent v" in capsys.readouterr().out


def test_legacy_cli_honors_argv():
    legacy_cli = _load_legacy_cli()
    seen: dict[str, object] = {}

    def fake_run(**kwargs):
        seen.update(kwargs)

    assert legacy_cli.main(["--query", "hi"], run=fake_run) == 0
    assert seen["query"] == "hi"
    seen.clear()

    assert legacy_cli.main(["--list-tools"], run=fake_run) == 0
    assert seen["list_tools"] is True
    seen.clear()

    # A bare invocation prints help and never reaches the runner (no demo turn).
    assert legacy_cli.main([], run=fake_run) == 0
    assert seen == {}


def test_legacy_cli_source_has_no_upstream_prog():
    source = LEGACY_CLI.read_text(encoding="utf-8")
    assert 'prog="qubit-agent"' in source
    assert 'prog="hermes-agent"' not in source
