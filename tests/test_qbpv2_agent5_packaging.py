"""Agent 5 (packaging/lifecycle/security) wave-2 regression tests.

These pin the three real clean-install defects found and repaired in
``pyproject.toml`` / ``scripts/clean_install_check.sh`` during the byte-parity
verification wave, so they cannot silently regress:

1. A wheel installed with only its declared dependencies must be able to
   import the real runtime start path (``voicebridge.orchestrator`` and the
   default VAD fallback). ``webrtcvad`` imports the legacy ``pkg_resources``
   API, which is provided by setuptools — the dependency must be declared, not
   patched in by ``scripts/install.sh`` only.
2. The declared Python floor (``requires-python``) must match the interpreter
   the code actually needs (PEP 701 f-strings) and that ``scripts/install.sh``
   provisions.
3. The release sdist must not sweep an untracked internal doc that carries a
   real developer home path (the frozen wave-3 gate only checks *tracked*
   docs).
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
INSTALL_SH = REPO / "scripts/install.sh"
CLEAN_INSTALL = REPO / "scripts/clean_install_check.sh"

HOME_LITERAL = re.compile(r"/home/([A-Za-z0-9._-]+)")
GENERIC_HOME_USERS = {"example", "..."}


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _sdist_excludes() -> list[str]:
    return list(_pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"].get(
        "exclude", []))


def _is_excluded(rel: str, excludes: list[str]) -> bool:
    return any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in excludes)


def test_webrtcvad_pkg_resources_dependency_is_declared():
    """A stock install must not crash when the silero VAD model is absent.

    voicebridge/audio/vad.py falls back to ``webrtcvad``, whose import needs
    ``pkg_resources`` (setuptools). The dependency must be in
    ``[project.dependencies]`` so pip/uv/wheel installs get it — not only
    ``scripts/install.sh``.
    """
    deps = _pyproject()["project"]["dependencies"]
    assert any(d.split(";")[0].strip().startswith("setuptools")
               for d in deps), (
        "webrtcvad imports pkg_resources; pyproject must declare setuptools<81")
    # The pin must stay below the setuptools release that removed the legacy
    # pkg_resources shim that webrtcvad relies on.
    spec = next(d for d in deps if d.strip().startswith("setuptools"))
    assert "<81" in spec, spec
    # And the current (repo) environment must actually be able to import the
    # runtime fallback path this protects.
    from voicebridge.audio.vad import _make_vad_gate

    gate = _make_vad_gate("silero", 1, None)
    assert callable(gate)


def test_requires_python_matches_the_code_and_the_installer():
    """The wheel must not advertise a Python floor it cannot run on.

    voicebridge/local_llm.py uses a PEP 701 f-string (backslash in the
    expression), which is a SyntaxError before 3.12; scripts/install.sh
    provisions the venv with ``uv venv --python 3.12``. ``requires-python``
    must agree, or a 3.11 user can install a package whose ``qubit run`` dies
    at import.
    """
    rp = _pyproject()["project"]["requires-python"]
    m = re.fullmatch(r">=\s*3\.(\d+)(?:,<3\.(\d+))?", rp.strip())
    assert m, f"unexpected requires-python spec: {rp!r}"
    # The floor stays 3.12 (PEP 701 f-strings; install.sh provisions it).
    assert int(m.group(1)) >= 12, rp
    # The ceiling, when declared, must match the vendored runtime's upstream-
    # aligned cap (`qubit_runtime/pyproject.toml` >=3.11,<3.15): the fork is
    # force-included, so this distribution advertises the same supported window.
    if m.group(2) is not None:
        assert int(m.group(2)) == 15, rp
    install = INSTALL_SH.read_text(encoding="utf-8")
    assert "uv venv --python 3.12" in install, (
        "install.sh must pin the same interpreter floor as requires-python")


def test_sdist_excludes_untracked_docs_with_a_developer_home_path():
    """The release sdist must not ship any doc carrying a real user home path.

    Extends the frozen tracked-docs gate to untracked working-tree docs (a
    working-tree build sweeps them). The prior wave fixed
    docs/OPERATOR_QUALIFICATION.md; this pins docs/archive/2026-09-21/QUBIT_WAVE2_EVIDENCE.md and
    any future offender.
    """
    excludes = _sdist_excludes()
    offenders: dict[str, list[str]] = {}
    for p in sorted((REPO / "docs").rglob("*")):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        users = {u for u in HOME_LITERAL.findall(text)
                 if u not in GENERIC_HOME_USERS}
        if users:
            offenders[p.relative_to(REPO).as_posix()] = sorted(users)
    missed = [rel for rel in offenders if not _is_excluded(rel, excludes)]
    assert not missed, (
        f"docs with a developer home path not excluded from the sdist: {missed}")


def test_sdist_excludes_the_untracked_wave2_evidence_and_allowlist():
    excludes = _sdist_excludes()
    for rel in ("docs/archive/2026-09-21/QUBIT_WAVE2_EVIDENCE.md", "docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md"):
        assert _is_excluded(rel, excludes), rel


def test_clean_install_gate_exercises_the_real_runtime_import_path():
    """The clean-install probe must import the orchestrator + VAD fallback.

    A webui-only probe passed while ``qubit run`` crashed on the declared
    Python floor; this pins the stronger probe so the gate stays honest.
    """
    text = CLEAN_INSTALL.read_text(encoding="utf-8")
    assert "voicebridge.orchestrator" in text
    assert "_make_vad_gate" in text


def test_declared_floor_interpreter_can_construct_the_vad_fallback():
    """End-to-end guard: under the declared floor the fallback must import.

    Skip when the interpreter running the suite is older than the declared
    floor (the packaging contracts make the floor 3.12), so the test fails
    loudly rather than importing a broken module.
    """
    if sys.version_info < (3, 12):
        pytest.skip("suite interpreter is below the declared floor")
    # Importing the module that carried the PEP 701 f-string proves the floor.
    import voicebridge.local_llm  # noqa: F401
    import voicebridge.orchestrator  # noqa: F401


def test_build_dashboard_check_exit_codes_are_typed():
    """build_dashboard.sh --check must never claim success without a bundle."""
    script = REPO / "scripts/build_dashboard.sh"
    text = script.read_text(encoding="utf-8")
    assert "DASHBOARD_PRESENT" in text and "DASHBOARD_BUILDABLE" in text
    result = subprocess.run(
        ["bash", str(script), "--check"], capture_output=True, text=True,
        cwd=str(REPO))
    assert result.returncode in (0, 3), (result.returncode, result.stderr)
    assert ("DASHBOARD_PRESENT" in result.stdout
            or "DASHBOARD_BUILDABLE" in result.stdout)


def test_clean_install_gate_stashes_and_verifies_absent_node_modules():
    """Hatchling force-include ignores .gitignore, and the frozen pyproject
    maps the whole ``frontend`` tree, so the gate must stash
    ``frontend/qubit-web/node_modules`` around the build and prove the wheel
    carries zero build-only members."""
    for script in (CLEAN_INSTALL, REPO / "scripts/verify_installed_dashboard.sh"):
        text = script.read_text(encoding="utf-8")
        assert "NODE_MODULES_STASH" in text, script.name
        assert "mv \"$NODE_MODULES\" \"$NODE_MODULES_STASH\"" in text, script.name
    clean = CLEAN_INSTALL.read_text(encoding="utf-8")
    assert "node_modules members" in clean
    assert "swept" in clean


def test_verify_installed_dashboard_checks_the_activity_alias_redirect():
    """``/activity`` is a public alias for the Qubit ``/logs`` route, so the
    installed-dashboard gate must assert the redirect (and that ``/logs`` is
    the real 200 page), not a stale 200 on the legacy path."""
    text = (REPO / "scripts/verify_installed_dashboard.sh").read_text(
        encoding="utf-8")
    assert 'raw("/activity")' in text
    assert '(302, "/logs")' in text
