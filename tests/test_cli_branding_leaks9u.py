"""CLI deep-rebrand leak repairs (audit wave 9U+).

An independent audit found user-visible upstream branding still rendered from
``qubit_runtime/qubit_cli``. This wave repairs the rendered strings (prints,
tips, help/error text, install/service banners) and closes the deterministic
debrand detector's blind spots: the compound ``NousResearch`` org token (which
has no word boundary, so ``\\bNous\\b`` never matched it) and branded upstream
URLs / service-artifact names — without rewriting wire or provenance
identifiers.

Each behavioural test drives the real call site; the few print literals that
cannot be invoked in isolation are pinned by their exact text in the owning
file (the established branding-scan pattern in this suite).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "qubit_runtime"))

CLI = REPO / "qubit_runtime" / "qubit_cli"
_BRAND = re.compile(r"hermes|nous|openclaw", re.IGNORECASE)


def _read(name: str) -> str:
    return (CLI / name).read_text(encoding="utf-8")


def _load_debrand():
    spec = importlib.util.spec_from_file_location(
        "qubit_debrand_probe", REPO / "scripts" / "qubit_debrand.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------- tips
def test_tips_fixed_entries_have_no_upstream_branding():
    from qubit_cli.tips import TIPS

    joined = "\n".join(TIPS)
    for leak in ("NousResearch", "HERMES_DEV=1", "~/.hermes/.env",
                 "The skills quarantine at ~/.hermes"):
        assert leak not in joined, leak
    # repaired entries remain present and Qubit-branded
    assert any("~/.config/qubit-voicebridge/.env" in t for t in TIPS)
    assert any("trusted organizations" in t for t in TIPS)
    assert any("developer-mode environment flag" in t for t in TIPS)
    assert any("skills quarantine under the Qubit home" in t for t in TIPS)


# ------------------------------------------------------------ console engine
def test_console_argparse_error_is_qubit_prefixed(monkeypatch):
    from qubit_cli import console_engine as ce

    fake = types.SimpleNamespace(builder=lambda sp: None)
    monkeypatch.setattr(ce.importlib, "import_module", lambda name: fake)
    surface = ce._CliSurface(kind="registered", module="fake", builder="builder")
    parser = surface.build("root", live=False)
    with pytest.raises(ce.ConsoleCommandError) as ei:
        parser.error("boom")
    assert str(ei.value) == "qubit: boom"


# ------------------------------------------------------- update: upstream offer
def test_upstream_offer_prints_no_brand(capsys):
    from qubit_cli import update_cmd_git as g

    assert g._offer_upstream_remote(
        [], Path("."), assume_yes=True, input_fn=None) is False
    out = capsys.readouterr().out
    assert not _BRAND.search(out), out
    assert "official Qubit repository" in out


# --------------------------------------------------- update: ACP launcher repair
def test_acp_launcher_is_qubit_branded(tmp_path, monkeypatch, capsys):
    # Source-checkout behavior: no recorded pm store interpreter exists, so the
    # launcher owner (qubit_cli._launchers.expose_cli) reports a typed skip and
    # the historical update_cmd_maint._ensure_acp_launcher export delegates
    # without writing anything host-side. Assert the honest behavior (typed
    # skip, brand-free output, no launcher created) -- never a fabricated write.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "qubit").write_text("#!/bin/sh\n", encoding="utf-8")

    from qubit_cli import _launchers
    from qubit_cli import update_cmd_maint as m
    from qubit_cli.update_cmd import _m

    # A source checkout has no recorded pm store interpreter.
    monkeypatch.setattr(_launchers, "resolve_store_python", lambda root: None)

    before = sorted(p.name for p in bin_dir.iterdir())
    assert m._ensure_acp_launcher() is None
    out = capsys.readouterr().out
    assert not _BRAND.search(out), out
    assert sorted(p.name for p in bin_dir.iterdir()) == before
    assert not (bin_dir / "qubit-acp").exists()

    # The launcher policy owner surfaces the typed skip, not a fake success.
    res = _launchers.expose_cli(_m().PROJECT_ROOT)
    assert res.get("ok") is True, res
    assert res.get("skipped") == "no-store-python", res


# -------------------------------------- remaining rendered print literals
def test_update_and_tools_prints_are_debranded():
    # ``update_cmd_deps.py`` never existed in the pinned upstream (nor here);
    # assert the real de-branded observables in the modules that do exist.
    maint = _read("update_cmd_maint.py")
    assert "hermes-agent.nousresearch.com" not in maint
    assert "Installed hermes-acp launcher" not in maint
    assert "hermes-acp launcher self-heal failed" not in maint
    assert 'bin_dir / "hermes-acp"' not in maint
    assert 'bin_dir / "hermes"' not in maint
    assert "Skill curator" in maint
    assert "qubit curator run --dry-run" in maint

    upd = _read("update_cmd.py")
    assert "handed off by hermes.exe" not in upd
    assert "Another hermes.exe is running" not in upd
    assert "hermes-agent.nousresearch.com/install.sh" not in upd
    assert "Qubit update pipeline" in upd

    fleet = _read("update_cmd_fleet.py")
    assert "(hermes.service)" not in fleet
    assert "hermes-gateway.service for the bot token" not in fleet

    tools = _read("tools_config.py")
    assert "nousresearch.com/docs/user-guide/features/tools" not in tools
    assert "next 'hermes'" not in tools
    assert "next 'qubit'" in tools

    profile = _read("profile_cmd.py")
    assert "Switched to: default (~/.hermes)" not in profile

    pairing = _read("pairing.py")
    assert "entry from ~/.hermes/platforms/pairing" not in pairing

    git = _read("update_cmd_git.py")
    assert "_UPSTREAM_ADD_CMD" not in git
    assert "may miss updates from NousResearch" not in git


# ------------------------------------------------------- debrand detector
def test_debrand_detector_closes_the_nousresearch_blind_spot():
    mod = _load_debrand()
    found = dict(mod._surface_tokens(
        "updates from NousResearch/hermes-agent (hermes-acp)"))
    assert found.get("NousResearch") == "nousresearch_org"
    assert found.get("hermes-agent") == "brand_service_name"
    assert found.get("hermes-acp") == "brand_service_name"

    urls = [cls for _, cls in mod._surface_tokens(
        "docs at https://hermes-agent.nousresearch.com/x")]
    assert "upstream_url" in urls

    # detection is report-only: wire/provenance identifiers survive the pass.
    for wire in ("X-Hermes-Session-Token", "__HERMES_BASE_PATH__",
                 "HERMES_HOME", "NousResearch/hermes-media-studio"):
        new, _classes = mod._transform(f"value {wire} here")
        assert wire in new, (wire, new)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
