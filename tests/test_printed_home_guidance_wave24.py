"""Printed home/credential guidance no longer points at ``~/.hermes/.env``.

Wave 24 de-brands the remaining *user-facing* CLI copy in the vendored
``qubit_runtime/qubit_cli`` tree: printed help/error/notice strings that told
users to put credentials in the reference home (``~/.hermes/.env``) or offered
the upstream ``portal.nousresearch.com`` / ``discord.gg/NousResearch`` support
links. The canonical credential file is the Qubit home ``.env``
(``qubit_cli/config.py::get_env_path`` / ``qubit_constants.display_qubit_home``)
and the canonical support/repo target is the Qubit GitHub repository.

The assertions are scoped to the exact format strings this wave changed, so
preserved internal docstrings, ``~/.hermes`` read-old migration paths, wire
identifiers, and functional upstream endpoints (e.g. the published Docker image
``nousresearch/hermes-agent``) are deliberately not flagged here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: (relative path, legacy user-facing literal that must be gone, replacement
#: literal that must be present). The replacement literals include the helper
#: call / Qubit-home path so the test proves the copy was actually re-pointed,
#: not merely deleted.
CASES = (
    (
        "qubit_runtime/qubit_cli/agent_import.py",
        "add them to ~/.hermes/.env",
        "{display_qubit_home()}/.env",
    ),
    (
        "qubit_runtime/qubit_cli/agent_import.py",
        "via 'qubit setup' or ~/.hermes/.env",
        "{display_qubit_home()}/.env",
    ),
    (
        "qubit_runtime/qubit_cli/gateway_setup_wizard.py",
        "account_id/token in ~/.hermes/.env",
        "{_dhh()}/.env",
    ),
    (
        "qubit_runtime/qubit_cli/gateway_setup_wizard.py",
        "Set these env vars in ~/.hermes/.env",
        "Set these env vars in {_dhh()}/.env",
    ),
    (
        "qubit_runtime/qubit_cli/auth_spotify.py",
        "Saved HERMES_SPOTIFY_CLIENT_ID to ~/.hermes/.env",
        "Saved HERMES_SPOTIFY_CLIENT_ID to {display_qubit_home()}/.env",
    ),
    (
        "qubit_runtime/qubit_cli/memory_setup.py",
        "Install a plugin to ~/.hermes/plugins/",
        "Install a plugin to {display_qubit_home()}/plugins/",
    ),
    (
        "qubit_runtime/qubit_cli/memory_setup.py",
        "do not inherit ~/.hermes/.env",
        "do not inherit {display_qubit_home()}/.env",
    ),
    (
        "qubit_runtime/qubit_cli/memory_setup.py",
        "memory plugin to ~/.hermes/plugins/",
        "memory plugin to {display_qubit_home()}/plugins/",
    ),
    (
        "qubit_runtime/qubit_cli/config.py",
        "edit ~/.hermes/.env ",
        "edit ~/.qubit/.env ",
    ),
    (
        "qubit_runtime/qubit_cli/config.py",
        "check ~/.hermes/.env",
        "check ~/.qubit/.env",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/gateway.py",
        "into ~/.hermes/.env. ",
        "into ~/.qubit/.env. ",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/gateway.py",
        "in ~/.hermes/.env and forwarded",
        "in ~/.qubit/.env and forwarded",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/dashboard.py",
        "HERMES_DASHBOARD_OAUTH_CLIENT_ID into ~/.hermes/.env",
        "HERMES_DASHBOARD_OAUTH_CLIENT_ID into ~/.qubit/.env",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/peer.py",
        "to ~/.hermes/.env)",
        "to ~/.qubit/.env)",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/peer.py",
        "in ~/.hermes/.env\")",
        "in ~/.qubit/.env\")",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/peer.py",
        "stored in ~/.hermes/.env",
        "stored in ~/.qubit/.env",
    ),
    (
        "qubit_runtime/qubit_cli/subcommands/peer.py",
        "credential in ~/.hermes/.env",
        "credential in ~/.qubit/.env",
    ),
    (
        "qubit_runtime/qubit_cli/runtime_provider.py",
        "in ~/.hermes/.env, ",
        "in {display_qubit_home()}/.env, ",
    ),
    (
        "qubit_runtime/qubit_cli/setup_tts.py",
        "until removed from ~/.hermes/.env.",
        "until removed from {_dhh()}/.env.",
    ),
    (
        "qubit_runtime/qubit_cli/debug.py",
        "https://portal.nousresearch.com/help",
        "github.com/{install_source.official_repository()}",
    ),
    (
        "qubit_runtime/qubit_cli/debug.py",
        "https://discord.gg/NousResearch",
        "github.com/{install_source.official_repository()}",
    ),
    (
        "qubit_runtime/qubit_cli/setup_quick.py",
        "Sign up: https://portal.nousresearch.com/manage-subscription",
        "Learn more: {_qubit_base_url()}",
    ),
)

#: Every owned CLI file in this wave (scan scope proof).
OWNED_FILES = (
    "qubit_runtime/qubit_cli/agent_import.py",
    "qubit_runtime/qubit_cli/gateway_setup_wizard.py",
    "qubit_runtime/qubit_cli/auth_spotify.py",
    "qubit_runtime/qubit_cli/memory_setup.py",
    "qubit_runtime/qubit_cli/config.py",
    "qubit_runtime/qubit_cli/subcommands/gateway.py",
    "qubit_runtime/qubit_cli/subcommands/dashboard.py",
    "qubit_runtime/qubit_cli/subcommands/peer.py",
    "qubit_runtime/qubit_cli/runtime_provider.py",
    "qubit_runtime/qubit_cli/setup_tts.py",
    "qubit_runtime/qubit_cli/plugins_cmd.py",
    "qubit_runtime/qubit_cli/auth.py",
    "qubit_runtime/qubit_cli/debug.py",
    "qubit_runtime/qubit_cli/setup_quick.py",
)


def _read(rel: str) -> str:
    path = REPO / rel
    assert path.is_file(), f"owned file missing: {rel}"
    return path.read_text(encoding="utf-8")


def test_scan_scope_is_complete() -> None:
    for rel in OWNED_FILES:
        assert (REPO / rel).is_file(), rel


@pytest.mark.parametrize("rel,legacy,replacement", CASES,
                         ids=[f"{rel}:{legacy[:32]}" for rel, legacy, _ in CASES])
def test_user_facing_strings_are_repointed(rel: str, legacy: str, replacement: str) -> None:
    src = _read(rel)
    assert legacy not in src, (
        f"{rel} still directs users to the reference home with: {legacy!r}")
    assert replacement in src, (
        f"{rel} is missing the Qubit-home replacement {replacement!r}")


def test_no_user_facing_portal_or_discord_links_in_owned_files() -> None:
    """No owned CLI file prints an upstream portal/discord support URL."""
    for rel in OWNED_FILES:
        src = _read(rel)
        assert "discord.gg/NousResearch" not in src, rel
        # The portal host itself is a functional auth endpoint in auth.py; only the
        # printed support/sign-up URLs must be gone.
        assert "portal.nousresearch.com/help" not in src, rel
        assert "portal.nousresearch.com/manage-subscription" not in src, rel
