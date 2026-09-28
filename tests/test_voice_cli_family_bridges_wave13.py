"""Wave 13 — CLI-family voice read bridges, continued.

Wave 12 bridged the bare CLI-family intents (``qubit logs``, ``qubit config``
…). The parity inventory still names the next tranche — the
``qubit <family> <read-sub>`` intents (``qubit runtime history``, ``qubit
actions list``, ``qubit sessions stats`` …) — which resolved nowhere.

Wave 13 adds additive ``CLI_BRIDGE_REGISTRY`` entries binding each to the SAME
read-only backend the CLI/dashboard already call. Scope:

* every new intent resolves through the production ``resolve()`` to its
  intended entry, bound to a real ``_BACKENDS`` callable (never
  ``voice_unsupported``);
* the read entries carry no confirmation and no mutation; the handful of
  genuinely mutating subcommands that had no voice path (``resume``,
  ``runtime repair``, ``sessions rename``, ``project use``, ``kanban create``,
  ``documents extract``) are confirm-tier through the one shared journal;
* anchored ``rx:`` aliases bind ONLY the exact read — a longer/mutating intent
  sharing the prefix stays unresolved (a visible gap) instead of misrouting;
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

# intent -> the wave-13 entry id it must resolve to.
WAVE13_BINDINGS = {
    "qubit resume": "cli_resume",
    "qubit auth": "cli_auth",
    "qubit auth list": "cli_auth",
    "qubit worker": "cli_worker",
    "qubit monitoring": "cli_monitoring",
    "qubit model": "cli_model",
    "qubit security": "cli_security",
    "qubit diagnostics": "cli_diagnostics",
    "qubit prompt size": "cli_prompt_size",
    "qubit runtime history": "cli_runtime_history",
    "qubit runtime repair": "cli_runtime_repair",
    "qubit actions": "cli_actions_list",
    "qubit actions list": "cli_actions_list",
    "qubit checkpoints": "cli_checkpoints",
    "qubit checkpoints list": "cli_checkpoints",
    "qubit experiments": "cli_experiments_list",
    "qubit experiments list": "cli_experiments_list",
    "qubit verified": "cli_verified_list",
    "qubit verified skills": "cli_verified_list",
    "qubit opencode": "cli_opencode_list",
    "qubit opencode list": "cli_opencode_list",
    "qubit ghost": "cli_ghost_list",
    "qubit ghost list": "cli_ghost_list",
    "qubit transfers": "cli_transfers_status",
    "qubit transfers status": "cli_transfers_status",
    "qubit shares": "cli_shares_list",
    "qubit shares list": "cli_shares_list",
    "qubit questions pending": "cli_questions_pending",
    "qubit branding verify": "cli_branding_verify",
    "qubit sessions stats": "cli_sessions_stats",
    "qubit sessions rename 12 invoices": "cli_session_rename",
    "qubit session rename 12 invoices": "cli_session_rename",
    "qubit mcp": "cli_mcp_list",
    "qubit mcp list": "cli_mcp_list",
    "qubit mcp catalog": "cli_mcp_catalog",
    "qubit plugins": "cli_plugins_list",
    "qubit plugins list": "cli_plugins_list",
    "qubit plugins catalog": "cli_plugins_catalog",
    "qubit peers": "cli_peer_list",
    "qubit peer list": "cli_peer_list",
    "qubit msg accounts": "cli_msg_accounts",
    "qubit project list": "cli_projects_list",
    "qubit projects list": "cli_projects_list",
    "qubit project use shop": "cli_project_use",
    "qubit kanban boards": "cli_kanban_boards",
    "qubit kanban create buy milk": "cli_kanban_create",
    "qubit autonomy list": "cli_autonomy_list",
    "qubit documents extract voicebridge/__x.txt": "cli_documents_extract",
    "qubit web fetch https://example.com": "cli_web_fetch",
    "qubit env": "cli_env",
    "qubit profile check brain": "cli_profile_check",
}

WAVE13_ENTRY_IDS = {v for v in WAVE13_BINDINGS.values()}

# The read entries (no confirmation, no mutation). The rest of wave 13 are
# confirm-tier mutations that had no voice path before.
WAVE13_READ_IDS = {
    "cli_auth", "cli_worker", "cli_monitoring", "cli_model", "cli_security",
    "cli_diagnostics", "cli_prompt_size", "cli_runtime_history",
    "cli_actions_list", "cli_action_receipts", "cli_checkpoints",
    "cli_experiments_list", "cli_verified_list", "cli_opencode_list",
    "cli_ghost_list", "cli_transfers_list", "cli_transfers_status",
    "cli_shares_list", "cli_questions_pending", "cli_branding_verify",
    "cli_sessions_stats", "cli_mcp_list", "cli_mcp_catalog",
    "cli_plugins_list", "cli_plugins_catalog", "cli_peer_list",
    "cli_msg_accounts", "cli_projects_list", "cli_kanban_boards",
    "cli_autonomy_list", "cli_files_inspect", "cli_web_fetch", "cli_env",
    "cli_profile_check",
}

# The wave-12 read bridges must stay untouched.
WAVE12_ENTRY_IDS = {
    "cli_logs", "cli_insights", "cli_acp", "cli_lsp", "cli_browser",
    "cli_hooks", "cli_worktree", "cli_completion", "cli_config", "cli_commands",
    "cli_curator", "cli_themes", "cli_tools", "cli_pairing", "cli_webhooks",
    "cli_projects", "cli_reports", "cli_sessions", "cli_sessions_search",
    "cli_skills", "cli_memory_search", "cli_egress", "cli_usage",
    "cli_receipts", "cli_logbook", "cli_telemetry", "cli_media",
    "cli_documents", "cli_acp_client", "cli_automation", "cli_approvals",
    "cli_node_host", "cli_channels", "cli_permissions", "cli_flags",
    "cli_secrets", "cli_vision", "cli_proxy", "cli_audio", "cli_device",
    "cli_runtime", "cli_frontier", "cli_brain",
}

# Longer/mutating intents that share a read bridge's prefix must NOT be
# captured by the anchored read alias (they are genuine gaps, not list reads).
OVER_CAPTURE_MUST_STAY_UNRESOLVED = {
    "qubit actions execute kill-target": "cli_actions_list",
    "qubit actions cancel x": "cli_actions_list",
    "qubit actions check x": "cli_actions_list",
    "qubit runtime upgrade opencode": "cli_runtime_history",
    "qubit runtime install opencode": "cli_runtime_history",
    "qubit runtime rollback opencode": "cli_runtime_history",
    "qubit plugins install /tmp/x.json": "cli_plugins_list",
    "qubit plugins enable x": "cli_plugins_list",
    "qubit plugins remove x": "cli_plugins_list",
    "qubit mcp install airtable": "cli_mcp_list",
    "qubit mcp remove x": "cli_mcp_list",
    "qubit sessions delete 3": "cli_sessions_stats",
    "qubit sessions prune": "cli_sessions_stats",
    "qubit transfers fetch /tmp/a": "cli_transfers_status",
    "qubit shares publish 3": "cli_shares_list",
    "qubit opencode run foo": "cli_opencode_list",
    "qubit ghost execute x": "cli_ghost_list",
    "qubit kanban edit 3": "cli_kanban_boards",
    "qubit project archive shop": "cli_projects_list",
    "qubit project remove-folder x": "cli_projects_list",
    "qubit msg account add x": "cli_msg_accounts",
    "qubit auth logout": "cli_auth",
    "qubit auth remove x": "cli_auth",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_wave13_entries_are_registered():
    from voicebridge import voice_commands as VC

    for entry_id in WAVE13_ENTRY_IDS:
        assert entry_id in VC.CLI_BRIDGE_REGISTRY, entry_id
    # The wave-12 entries are not disturbed.
    assert WAVE12_ENTRY_IDS <= set(VC.CLI_BRIDGE_REGISTRY)


def test_every_wave13_intent_resolves_to_a_real_backend():
    from voicebridge import voice_commands as VC

    misrouted = []
    for text, expected in WAVE13_BINDINGS.items():
        res = VC.resolve(text)
        got = res.entry.id if res.entry is not None else None
        if res.kind not in (VC.MATCH, VC.CLARIFY) or got != expected:
            misrouted.append((text, expected, res.kind, got))
            continue
        backend = res.entry.backend
        if backend not in VC._BACKENDS or backend == "voice_unsupported":
            misrouted.append((text, expected, "dead-backend", backend))
    assert not misrouted, f"wave-13 intents did not resolve: {misrouted}"


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


def test_wave13_read_entries_are_read_only_and_bound():
    from voicebridge import voice_commands as VC

    for entry_id in WAVE13_READ_IDS:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        assert entry.backend and entry.backend in VC._BACKENDS, entry_id
        assert entry.backend != "voice_unsupported", entry_id
        assert entry.confirm is False, f"{entry_id} must be read-only"
        assert entry.aliases and entry.examples and entry.help, entry_id
        assert entry.scope, entry_id


def test_wave13_mutations_are_confirmation_gated():
    """The genuinely mutating subcommands wave 13 binds are confirm-tier
    through the one shared journal — never a direct write by voice."""
    from voicebridge import voice_commands as VC

    mutating = {
        "cli_resume", "cli_runtime_repair", "cli_session_rename",
        "cli_project_use", "cli_kanban_create", "cli_documents_extract",
    }
    assert mutating <= WAVE13_ENTRY_IDS
    for entry_id in mutating:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        assert entry.confirm is True, f"{entry_id} must be confirm-gated"


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


def test_no_dead_backend_in_any_registry():
    from voicebridge import voice_commands as VC

    dead = []
    for registry in (VC.REGISTRY, VC.PLUGIN_FLEET_REGISTRY,
                     VC.MODEL_PROFILE_REGISTRY, VC.CLI_BRIDGE_REGISTRY):
        for entry in registry.values():
            if entry.backend not in VC._BACKENDS:
                dead.append((entry.id, entry.backend))
    assert dead == [], f"entries referencing unknown backends: {dead}"


def test_wave13_strings_have_no_legacy_brand():
    from voicebridge import voice_commands as VC

    offenders = []
    for entry_id in WAVE13_ENTRY_IDS:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        strings = [entry.id, entry.title, entry.help, entry.summary,
                   entry.backend, entry.split, *entry.aliases, *entry.examples]
        for key, value in entry.fixed or ():
            strings.extend([str(key), str(value)])
        for arg in entry.args:
            strings.extend([arg.name, arg.type, arg.clarifier,
                            *arg.choices])
        for value in strings:
            low = str(value).lower()
            for token in LEGACY_TOKENS:
                if token in low:
                    offenders.append((entry_id, token, value))
    assert offenders == [], f"legacy brand in wave-13 strings: {offenders}"


def test_representative_wave13_reads_execute_real_work(tmp_path, monkeypatch):
    """Reads execute through the one pipeline and return real backend payloads
    (not a refusal, not an empty stub)."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w13", source="cli")

    # auth -> the real credential-reference reader (names only)
    out = VC.execute(ctx, VC.resolve("qubit auth"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "auth" in out.receipt["result"]

    # env -> the real credential-reference reader (names only)
    out = VC.execute(ctx, VC.resolve("qubit env"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "secrets" in out.receipt["result"]

    # actions -> the real broker action inventory
    out = VC.execute(ctx, VC.resolve("qubit actions"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "actions" in out.receipt["result"]

    # mcp catalog -> the real reconciled catalog route
    out = VC.execute(ctx, VC.resolve("qubit mcp catalog"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    body = out.receipt["result"]["mcp_catalog"]
    assert body.get("state") == "OK" and body.get("http_status") == 200, body

    # plugins -> the real plugin-runtime record list
    out = VC.execute(ctx, VC.resolve("qubit plugins"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert "plugins_runtime" in out.receipt["result"]

    # web fetch -> the real brokered fetch of the reserved example domain
    out = VC.execute(ctx, VC.resolve("qubit web fetch https://example.com"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["web"].get("ok") is True


def test_wave13_mutations_stage_on_the_shared_journal(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w13", source="cli")

    for phrase in ("qubit resume", "qubit runtime repair",
                   "qubit sessions rename 12 invoices",
                   "qubit project use shop", "qubit kanban create buy milk",
                   "qubit documents extract voicebridge/__x.txt"):
        staged = VC.execute(ctx, VC.resolve(phrase))
        assert staged.kind == VC.NEEDS_CONFIRM, (phrase, staged.kind,
                                                  staged.speech)
        assert staged.confirmation_id, phrase


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
