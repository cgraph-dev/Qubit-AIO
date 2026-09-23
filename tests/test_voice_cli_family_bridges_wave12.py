"""Wave 12 — CLI-family voice read bridges.

The parity inventory names canonical CLI families (``qubit logs``,
``qubit acp``, ``qubit config`` …) as ``voice_intent`` candidates, but the
frozen wave-1..3 ``REGISTRY`` aliases are spoken phrases, so those intents
resolved nowhere (1100 unresolved rows in ``docs/generated/voice_coverage.json``).

Wave 12 adds additive ``CLI_BRIDGE_REGISTRY`` entries binding each family to
the SAME read-only backend the CLI/dashboard already call. Scope:

* every new intent resolves through the production ``resolve()`` to its
  intended entry, bound to a real ``_BACKENDS`` callable (never
  ``voice_unsupported``);
* the entries are reads only (no confirmation, no mutation);
* no alias collides across any registry and no new string leaks legacy brand;
* a representative set executes real backend work with a receipt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LEGACY_TOKENS = ("hermes", "nousresearch", "nous portal", "nousportal",
                 "openclaw", "nous research")

# intent -> the wave-12 entry id it must resolve to.
WAVE12_BINDINGS = {
    "qubit logs": "cli_logs",
    "qubit insights": "cli_insights",
    "qubit acp": "cli_acp",
    "qubit lsp": "cli_lsp",
    "qubit lsp list": "cli_lsp",
    "qubit browser": "cli_browser",
    "qubit hooks": "cli_hooks",
    "qubit hooks list": "cli_hooks",
    "qubit worktree": "cli_worktree",
    "qubit completions": "cli_completion",
    "qubit completion": "cli_completion",
    "qubit config": "cli_config",
    "qubit config show": "cli_config",
    "qubit commands": "cli_commands",
    "qubit commands list": "cli_commands",
    "qubit curator": "cli_curator",
    "qubit themes": "cli_themes",
    "qubit tools": "cli_tools",
    "qubit pairing": "cli_pairing",
    "qubit pairing list": "cli_pairing",
    "qubit webhooks": "cli_webhooks",
    "qubit projects": "cli_projects",
    "qubit reports": "cli_reports",
    "qubit sessions": "cli_sessions",
    "qubit sessions search invoice": "cli_sessions_search",
    "qubit session search invoice": "cli_sessions_search",
    "qubit skills list": "cli_skills",
    "qubit skills catalog": "cli_skills",
    "qubit memory search invoices": "cli_memory_search",
    "qubit search my memory invoices": "cli_memory_search",
    "qubit egress": "cli_egress",
    "qubit usage": "cli_usage",
    "qubit usage report": "cli_usage",
    "qubit receipts": "cli_receipts",
    "qubit logbook": "cli_logbook",
    "qubit telemetry": "cli_telemetry",
    "qubit media gen catalog": "cli_media",
    "qubit documents kinds": "cli_documents",
    "qubit acp client catalog": "cli_acp_client",
    "qubit automation flows": "cli_automation",
    "qubit approvals": "cli_approvals",
    "qubit nodes": "cli_node_host",
    "qubit paired nodes": "cli_node_host",
    "qubit channels": "cli_channels",
    "qubit permissions": "cli_permissions",
    "qubit flags": "cli_flags",
    "qubit secrets": "cli_secrets",
    "qubit vision": "cli_vision",
    "qubit proxy": "cli_proxy",
    "qubit audio": "cli_audio",
    "qubit device": "cli_device",
    "qubit runtime": "cli_runtime",
    "qubit frontier": "cli_frontier",
    "qubit brain": "cli_brain",
}

WAVE12_ENTRY_IDS = {v for v in WAVE12_BINDINGS.values()}

# The CLI_BRIDGE entries that predate wave 12 (must stay untouched).
W7_BRIDGE_IDS = {
    "plugins_capabilities", "skills_install",
    "guard_kanban_request_review", "guard_kanban_request_changes",
    "guard_kanban_reopen_review", "guard_kanban_link", "guard_kanban_unlink",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_wave12_entries_are_registered():
    from voicebridge import voice_commands as VC

    for entry_id in WAVE12_ENTRY_IDS:
        assert entry_id in VC.CLI_BRIDGE_REGISTRY, entry_id
    # The W7 bridges are not disturbed.
    assert W7_BRIDGE_IDS <= set(VC.CLI_BRIDGE_REGISTRY)


def test_every_wave12_intent_resolves_to_a_real_backend():
    from voicebridge import voice_commands as VC

    misrouted = []
    for text, expected in WAVE12_BINDINGS.items():
        res = VC.resolve(text)
        got = res.entry.id if res.entry is not None else None
        if res.kind not in (VC.MATCH, VC.CLARIFY) or got != expected:
            misrouted.append((text, expected, res.kind, got))
            continue
        backend = res.entry.backend
        if backend not in VC._BACKENDS or backend == "voice_unsupported":
            misrouted.append((text, expected, "dead-backend", backend))
    assert not misrouted, f"wave-12 intents did not resolve: {misrouted}"


# Longer/mutating intents that share a read bridge's prefix must NOT be
# captured by the anchored read alias (they are genuine gaps, not list reads).
OVER_CAPTURE_MUST_STAY_UNRESOLVED = {
    "qubit tools enable a2a": "cli_tools",
    "qubit tools disable": "cli_tools",
    "qubit config set": "cli_config",
    "qubit config get": "cli_config",
    "qubit browser navigate": "cli_browser",
    "qubit browser fill": "cli_browser",
    "qubit runtime install": "cli_runtime",
    "qubit runtime upgrade": "cli_runtime",
    "qubit themes set": "cli_themes",
    "qubit sessions rename": "cli_sessions",
    "qubit sessions delete": "cli_sessions",
    "qubit pairing approve": "cli_pairing",
    "qubit pairing create": "cli_pairing",
    "qubit hooks fire": "cli_hooks",
    "qubit hooks declare": "cli_hooks",
    "qubit egress start": "cli_egress",
    "qubit egress stop": "cli_egress",
    "qubit lsp install": "cli_lsp",
    "qubit lsp restart": "cli_lsp",
    "qubit brain account add": "cli_brain",
    "qubit brain profile set": "cli_brain",
    "qubit acp client send": "cli_acp_client",
    "qubit acp client handshake": "cli_acp_client",
    "qubit proxy start": "cli_proxy",
}


def test_read_bridges_do_not_over_capture_longer_intents():
    """Anchored ``rx:`` aliases bind only the exact read the backend performs;
    a longer or mutating intent with the same prefix must stay unresolved
    instead of silently resolving to a list read."""
    from voicebridge import voice_commands as VC

    captured = []
    for text, forbidden in OVER_CAPTURE_MUST_STAY_UNRESOLVED.items():
        res = VC.resolve(text)
        got = res.entry.id if res.entry is not None else None
        if got == forbidden:
            captured.append((text, forbidden))
    assert not captured, f"read bridge over-captured longer intents: {captured}"


def test_wave12_entries_are_read_only_and_bound():
    from voicebridge import voice_commands as VC

    for entry_id in WAVE12_ENTRY_IDS:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        assert entry.backend and entry.backend in VC._BACKENDS, entry_id
        assert entry.backend != "voice_unsupported", entry_id
        assert entry.confirm is False, f"{entry_id} must be read-only"
        assert entry.aliases and entry.examples and entry.help, entry_id
        assert entry.scope, entry_id


def test_no_alias_collision_across_all_registries():
    from voicebridge import voice_commands as VC

    owners: dict = {}
    for entry in VC._iter_entries():
        for alias in entry.aliases:
            owners.setdefault(alias, []).append(entry.id)
    collisions = {a: o for a, o in owners.items() if len(o) > 1}
    assert collisions == {}, f"voice alias collisions: {collisions}"
    ids = [e.id for e in VC._iter_entries()]
    assert len(ids) == len(set(ids)), "duplicate voice entry ids"


def test_wave12_strings_have_no_legacy_brand():
    from voicebridge import voice_commands as VC

    offenders = []
    for entry_id in WAVE12_ENTRY_IDS:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        strings = [entry.id, entry.title, entry.help, entry.summary,
                   entry.backend, entry.split, *entry.aliases, *entry.examples]
        for value in strings:
            low = str(value).lower()
            for token in LEGACY_TOKENS:
                if token in low:
                    offenders.append((entry_id, token, value))
    assert offenders == [], f"legacy brand in wave-12 strings: {offenders}"


def test_representative_wave12_reads_execute_real_work(tmp_path, monkeypatch):
    """Reads execute through the one pipeline and return real backend payloads
    (not a refusal, not an empty stub)."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w12", source="cli")

    # logs -> {"logs": [...]} from the real events.jsonl reader
    out = VC.execute(ctx, VC.resolve("qubit logs"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "logs" in out.receipt["result"]

    # commands -> the real registered voice-command inventory
    out = VC.execute(ctx, VC.resolve("qubit commands"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    body = out.receipt["result"]["commands"]
    assert isinstance(body, list) and body, body

    # tools -> the real configurable toolset catalog
    out = VC.execute(ctx, VC.resolve("qubit tools"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "tools" in out.receipt["result"]

    # config -> the real provider profile registry
    out = VC.execute(ctx, VC.resolve("qubit config"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
