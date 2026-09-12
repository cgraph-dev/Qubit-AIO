"""F3 — surface accounting for the last open Qubit code-side ledger rows.

These fourteen Tier-2 dashboard extras each had a real backend, a real CLI
subcommand, and a real dashboard op, but ``dashboard_ops`` falsely declared
them voice-applicable while the spoken surface actually lives in the canonical
``voice_commands.REGISTRY`` entry under a different id. The generator therefore
reported ``voice=""`` and left every row ``PARTIAL`` (a fake gap).

The correction is honest, per the owner directive: the Tier-2 extra declares
only the surfaces it really owns (CLI + dashboard, or dashboard-only for the
editor typed refusals) and names its canonical spoken counterpart in
``voice_guard``. No duplicate/fake voice entry is introduced.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# (extra_id, expected declared interfaces, canonical spoken registry id)
CORRECTED = {
    # Editor typed refusals: dashboard-only (no separate spoken path is
    # meaningful; the real voice surface is the *_refusal refusal entry).
    "acp_accept_hooks": (("dashboard",), "acp_accept_hooks_refusal"),
    "acp_serve": (("dashboard",), "acp_serve_refusal"),
    "lsp_install": (("dashboard",), "lsp_install_refusal"),
    # Real CLI + dashboard extras; the canonical voice entry speaks for them.
    "brain_export": (("cli", "dashboard"), "brain_profile_export"),
    "brain_sweep": (("cli", "dashboard"), "brain_profile_sweep"),
    "import_agent_import": (("cli", "dashboard"), "import_refusal"),
    "kanban_delegated": (("cli", "dashboard"), "kanban_swarm"),
    "kanban_role_grant": (("cli", "dashboard"), "kanban_role_grant_voice"),
    "kanban_role_revoke": (("cli", "dashboard"), "kanban_role_revoke_voice"),
    "kanban_snapshot_restore": (
        ("cli", "dashboard"), "kanban_snapshot_restore_voice"),
    "kanban_widget_move": (
        ("cli", "dashboard"), "kanban_widget_move_voice"),
    "pets_downloads": (("dashboard",), "pets_downloads_refusal"),
    "plugins_remote": (("cli", "dashboard"), "plugins_remote_refusal"),
    "proxy_start": (("cli", "dashboard"), "proxy_start_refusal"),
}


def _load_rebuild():
    spec = importlib.util.spec_from_file_location(
        "rebuild_ledger_qubit_f3",
        REPO_ROOT / "scripts" / "rebuild_ledger_qubit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corrected_rows_declare_honest_interfaces():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cat = {o["id"]: o for o in OPS.catalog()}
    for op_id, (interfaces, guard) in CORRECTED.items():
        item = cat[op_id]
        assert item["tier"] == "extra", op_id
        assert tuple(item["interfaces"]) == interfaces, (op_id, item)
        # A no-voice claim must be typed, never silent.
        assert item["interface_reason"], op_id
        assert "voice" not in item["interfaces"], (op_id, item)
        # The canonical spoken counterpart is declared and real.
        assert item["voice_guard"] == guard, (op_id, item)
        assert guard in VC.REGISTRY, (op_id, guard)
        # No duplicate voice entry was invented for the Tier-2 id itself.
        assert op_id not in VC.REGISTRY, op_id


def test_declared_voice_guard_is_a_real_shared_backend_surface():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    for op_id, (_interfaces, guard) in CORRECTED.items():
        spec = OPS.EXTRA_OPS[op_id]
        entry = VC.REGISTRY[guard]
        # Same capability family (parity token) as the Tier-2 entry.
        assert set(entry.parity) & set(spec.parity), (op_id, guard)
        # A genuine spoken surface: it resolves from its own example.
        assert entry.backend in VC._BACKENDS, (op_id, guard)
        assert entry.examples and entry.aliases and entry.help, (op_id, guard)
        example = entry.examples[0]
        res = VC.resolve(example)
        assert res.kind in (VC.MATCH, VC.CLARIFY), (op_id, guard, res.kind)
        if res.kind == VC.CLARIFY:
            assert res.entry is not None and res.entry.id == guard


def test_catalog_voice_guard_is_consistent_across_build():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    # Every no-voice catalog op must name a real spoken guard; no silent miss.
    for item in OPS.catalog():
        op_id = item["id"]
        if "voice" in item["interfaces"]:
            continue
        guard = item.get("voice_guard") or ""
        if guard:
            assert guard in VC.REGISTRY, (op_id, guard)
        else:
            # fall back to the documented naming convention
            assert (f"{op_id}_refusal" in VC.REGISTRY
                    or f"{op_id}_voice" in VC.REGISTRY
                    or f"guard_{op_id}" in VC.REGISTRY), op_id


def test_generated_rows_close_as_implemented():
    """The in-memory ledger build marks every corrected row IMPLEMENTED with
    its declared (applicable) surfaces present and no fabricated voice."""
    rebuild = _load_rebuild()
    data = rebuild.build_ledger()
    rows = {r["id"]: r for r in data["rows"]}
    for op_id, (interfaces, guard) in CORRECTED.items():
        row = rows[op_id]
        assert row["status"] == "IMPLEMENTED", (op_id, row["status"])
        # `pets` is a deliberately absent CLI command (frozen boundaries
        # contract), so pets_downloads is an honest dashboard-only surface;
        # every other corrected op has both a CLI and a dashboard surface.
        if op_id == "pets_downloads":
            assert not row["cli"], op_id
        else:
            assert row["cli"], op_id
        assert row["dashboard"], op_id
        # Honest: the Tier-2 id itself has no voice surface; the guard does.
        assert row["voice"] == "", (op_id, row["voice"])
        assert "voice" not in interfaces
        _ = guard


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_editor_refusals_still_execute_typed_through_extras(tmp_path,
                                                            monkeypatch):
    from voicebridge import acp_lsp as AL
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    serve = OPS.run_extra_op(cfg, "acp_serve", {}, actor="dashboard:user")
    assert serve["state"] == "executed", serve
    assert serve["receipt"]["result"] == AL.acp_refuse_serve()
    hooks = OPS.run_extra_op(cfg, "acp_accept_hooks", {},
                             actor="dashboard:user")
    assert hooks["receipt"]["result"] == AL.acp_refuse_accept_hooks()
    install = OPS.run_extra_op(cfg, "lsp_install", {"server": "gopls"},
                               actor="dashboard:user")
    assert install["receipt"]["result"] == AL.lsp_refuse_install("gopls")
