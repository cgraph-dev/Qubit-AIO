"""Branding closure for the runtime/desktop surfaces touched by the 8u wave.

These checks pin the user-visible product name to Qubit on the surfaces that
carry it (desktop i18n + Electron strings, desktop/bootstrap package metadata,
and the specific runtime strings repaired in this wave) while proving the
wire/legal/provenance identifiers that MUST survive are still present.

Wire identifiers (``X-Qubit-Session-Token``, ``HERMES_*`` env names,
``hermes:...`` IPC channels, ``hermes-agent`` install paths, ``~/.hermes``,
the upstream provenance URL) are compatibility contract, not branding, and are
asserted present on purpose.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

DESKTOP = REPO / "qubit_runtime" / "apps" / "desktop"
I18N = DESKTOP / "src" / "i18n"
ELECTRON = DESKTOP / "electron"

#: User-visible product phrases that must never ship as Qubit branding.
BANNED_PHRASES = (
    "Hermes Desktop",
    "Hermes Agent",
    "Hermes Cloud",
    "Hermes Setup",
    "Hermes backend",
    "Hermes runtime",
    "Install Hermes",
    "About Hermes",
    "Native desktop shell for Hermes",
    "Nous Research",
    "Nous Portal",
    "Nous Cloud",
    "Nous staff",
    "Nous credits",
    "Nous-internal",
    "Nous-managed",
    "Copyright © 2026 Nous Research",
)

#: Phrases where any of these verbs follows the old product name.
BANNED_REGEXES = (
    re.compile(r"\bHermes\s+(?:uses|needs|captures|connects|accesses)\b"),
    re.compile(r"\bHermes\s+(?:Desktop|Agent|Cloud|backend|runtime|Setup)\b"),
    re.compile(r"\bNous\s+(?:Research|Portal|Cloud|staff|credits)\b"),
)

#: Identifiers that are compatibility/provenance contract and must survive.
#: `NousResearch` is deliberately NOT here: the desktop public surfaces were
#: de-branded (owner directive 2026-09-25) and no longer name the upstream owner.
#: The canonical repository replaces every user-facing GitHub/release/docs link.
ALLOWED_IDENTIFIERS = (
    "X-Qubit-Session-Token",
    "HERMES_DESKTOP_REMOTE_URL",
    "hermes:backend-exit",
    "hermes-agent",
    "~/.hermes",
    "__HERMES_",
)


def _source_surfaces() -> dict[str, list[Path]]:
    i18n = sorted(p for p in I18N.glob("*.ts") if ".test." not in p.name)
    electron = sorted(p for p in ELECTRON.glob("*.ts") if ".test." not in p.name)
    return {
        "desktop i18n": i18n,
        "desktop electron": electron,
        "desktop package.json": [DESKTOP / "package.json"],
        "desktop electron-builder config":
            [DESKTOP / "electron-builder.config.cjs"],
        "bootstrap-installer package.json":
            [REPO / "qubit_runtime" / "apps" / "bootstrap-installer"
             / "package.json"],
    }


def _read(paths: list[Path]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def test_surfaces_exist():
    surfaces = _source_surfaces()
    for name, paths in surfaces.items():
        assert paths, f"{name} surface resolved to no files"
        for p in paths:
            assert p.is_file(), p


def test_banned_public_phrases_absent_from_every_changed_surface():
    surfaces = _source_surfaces()
    failures: list[str] = []
    for name, paths in surfaces.items():
        text = _read(paths)
        for phrase in BANNED_PHRASES:
            if phrase in text:
                failures.append(f"{name}: {phrase!r}")
        for rx in BANNED_REGEXES:
            m = rx.search(text)
            if m:
                failures.append(f"{name}: {m.group(0)!r}")
    assert not failures, "upstream product branding leaked:\n" + "\n".join(failures)


def test_wire_legal_and_provenance_identifiers_survive():
    surfaces = _source_surfaces()
    blob = {name: _read(paths) for name, paths in surfaces.items()}
    all_text = "\n".join(blob.values())
    for ident in ALLOWED_IDENTIFIERS:
        assert ident in all_text, f"required identifier dropped: {ident!r}"


def test_desktop_shell_strings_are_qubit_branded():
    en = (I18N / "en.ts").read_text(encoding="utf-8")
    assert "Qubit Desktop is ready" in en
    assert "Qubit couldn't start" in en
    assert "Hermes Desktop is ready" not in en
    assert "Hermes couldn't start" not in en
    assert "Qubit Desktop" in en


def test_desktop_package_metadata_and_permissions_are_qubit_branded():
    """Desktop packaging identity is Qubit.

    In v0.21.5 the electron-builder block moved out of ``package.json`` into
    ``electron-builder.config.cjs`` and the name-shaped values moved into
    ``product-identity.cjs``; the branding contract follows them.
    """
    pkg = (DESKTOP / "package.json").read_text(encoding="utf-8")
    builder = (DESKTOP / "electron-builder.config.cjs").read_text(
        encoding="utf-8")
    identity = (DESKTOP / "product-identity.cjs").read_text(encoding="utf-8")
    assert '"author": "Qubit Labs"' in pkg
    assert "title: 'Install Qubit'" in builder
    assert "Native desktop shell for Qubit Agent." in builder
    assert "display: 'Qubit'" in identity
    assert "uses audio capture" in builder
    assert "accesses your music library" in builder
    assert '"Hermes uses' not in pkg
    assert '"Hermes needs' not in pkg


def test_about_panel_copyright_attribution_updated():
    main = (ELECTRON / "main.ts").read_text(encoding="utf-8")
    assert "Copyright © 2026 Qubit Labs" in main
    assert "Copyright © 2026 Nous Research" not in main


def test_runtime_marker_and_process_title_rebranded():
    run_agent = (REPO / "qubit_runtime" / "run_agent.py").read_text(
        encoding="utf-8")
    assert '"[qubit: tool call arguments were corrupted' in run_agent
    assert "[hermes-agent: tool call arguments" not in run_agent

    main = (REPO / "qubit_runtime" / "qubit_cli" / "main.py").read_text(
        encoding="utf-8")
    assert 'setproctitle.setproctitle("qubit")' in main
    assert 'setproctitle.setproctitle("hermes")' not in main
    assert 'b"qubit"' in main and 'b"hermes"' not in main

    gateway = (REPO / "qubit_runtime" / "qubit_cli" / "gateway.py").read_text(
        encoding="utf-8")
    assert "privileges to the 'qubit' user" in gateway
    assert "privileges to the 'hermes' user" not in gateway


def test_provenance_urls_are_not_falsified():
    """Desktop public links point at the canonical repo; support copy is de-branded.

    Owner directive 2026-09-25: every user-facing desktop URL is the canonical
    Qubit repository — the upstream owner/repo must not appear on the desktop
    surfaces. The debug-share support links printed to users are likewise the
    canonical Qubit repo, never the upstream portal/discord.
    """
    electron = _read(sorted(
        p for p in ELECTRON.glob("*.ts") if ".test." not in p.name))
    assert "github.com/cgraph-dev/Qubit-AIO" in electron
    assert "github.com/NousResearch/hermes-agent" not in electron
    pkg = (DESKTOP / "package.json").read_text(encoding="utf-8")
    assert "git+https://github.com/cgraph-dev/Qubit-AIO.git" in pkg
    assert "git+https://github.com/NousResearch/hermes-agent.git" not in pkg
    debug = (REPO / "qubit_runtime" / "qubit_cli" / "debug.py").read_text(
        encoding="utf-8")
    assert "github.com/{install_source.official_repository()}/issues" in debug
    assert "portal.nousresearch.com" not in debug
    assert "https://discord.gg/NousResearch" not in debug


@pytest.mark.parametrize("rel", [
    "qubit_runtime/run_agent.py",
    "qubit_runtime/qubit_cli/main.py",
    "qubit_runtime/qubit_cli/gateway.py",
])
def test_repaired_runtime_files_compile(rel):
    import py_compile

    py_compile.compile(str(REPO / rel), doraise=True)
