"""Wave 3 — independent voice-coverage closure for the union of implemented
CLI and dashboard operations.

This is the wave-3 verification layer over the frozen voice-completeness gate
(``docs/QUBIT_FULL_PARITY_CONTRACT.md`` / ``docs/VOICE_BRIDGE_SYSTEM.md``).  It
audits the production voice registry against the live CLI parser tree and the
dashboard op catalog, and then exercises the real pipeline end to end:

1. every live ``qubit <family>`` has a registry entry or an explicit,
   tested ``VOICE_IMPOSSIBLE`` reason (CLI/dashboard only) — never a silent
   gap;
2. every catalog op that claims a voice surface has a real ``REGISTRY`` entry
   (Tier-1, a dedicated ``*_refusal``/``*_voice`` guard, or a declared
   same-backend family entry), and every op that cannot be spoken carries an
   ``interface_reason`` plus a spoken typed refusal;
3. every registered alias resolves back to its own entry — so a spoken alias
   reaches the same production backend the CLI/dashboard does, not a neighbour;
4. reads execute with the same receipt result through voice and the shared
   ``dashboard_ops.run_op`` dispatcher (CLI/dashboard share it);
5. representative mutating ops stage exactly one confirmation that can be
   cancelled, replayed, emergency-stopped, and is session-isolated;
6. nothing fabricates success: guards refuse typed without staging, a staged
   write is never executed implicitly, and a gated/broken dependency yields a
   typed state instead of a fake OK.

No model, page, tool, or provider text can create a grant or confirm here: the
source gate and the single shared journal are exercised directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
LEDGER = REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.qubit.json"

# Tier-2 extras whose canonical spoken surface lives under a different Tier-1
# registry id (same backend). Mirrors the declarations the audit verifies and
# stays literal on purpose so the audit is independent of them.
_EXTRA_VOICE_FAMILY = {
    "brain_export": ("brain_profile_export",),
    "brain_sweep": ("brain_profile_sweep",),
    "import_agent_import": ("import_refusal",),
    "kanban_delegated": ("kanban_swarm", "kanban_watch", "kanban_dispatch"),
}
_EXTRA_VOICE_GUARD = {
    "export_state": "export_state_voice",
    "import_state": "import_state_voice",
}

# Representative reads: the spoken alias must reach the same production
# backend as the CLI/dashboard op and produce the identical result.
_READ_BATTERY = (
    ("qubit, git status", "git_status", "git"),
    ("qubit, computer status", "computer_status", "computer"),
    ("qubit, computer doctor", "computer_doctor", "computer"),
    ("qubit, computer permissions", "computer_permissions", "computer"),
    ("qubit, product status", "product_status", "product"),
    ("qubit, secret status", "secrets", "secrets"),
    ("qubit, brain vault status", "brain_vault_list", "brain"),
    ("qubit, list lessons", "lessons_list", "lessons"),
    ("qubit, list skills", "skills_list", "skills"),
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="wave3", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, actor="voice:user", session=session, source=source)


def _families():
    """Top-level CLI families; importing cli syncs CLI_COVERAGE (idempotent)."""
    from voicebridge.cli import build_parser

    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    return names


def _voice_surface(op_id):
    """The registry id that speaks for a catalog op, or None (a real gap)."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    if op_id in VC.REGISTRY:
        return op_id
    candidates = [f"{op_id}_refusal", f"{op_id}_voice", f"guard_{op_id}"]
    candidates.extend(_EXTRA_VOICE_FAMILY.get(op_id, ()))
    candidates.append(_EXTRA_VOICE_GUARD.get(op_id, ""))
    spec = OPS.EXTRA_OPS.get(op_id) or OPS.MODEL_PROFILE_OPS.get(op_id)
    if spec is not None:
        candidates.append(spec.voice_guard)
    for cand in candidates:
        if cand and cand in VC.REGISTRY:
            return cand
    return None


# ---------------------------------------------------------- 1. CLI families
def test_every_cli_family_has_a_voice_surface_or_typed_reason():
    from voicebridge import voice_commands as VC

    families = _families()
    assert set(VC.CLI_COVERAGE) == families, \
        f"CLI families drifted: {sorted(set(families) ^ set(VC.CLI_COVERAGE))}"

    for family, targets in VC.CLI_COVERAGE.items():
        for target in targets:
            assert target in VC.REGISTRY, (family, target)
        reasons = [k for k in VC.VOICE_IMPOSSIBLE
                   if k == family or k.startswith(f"{family}.")]
        assert targets or reasons, \
            f"{family}: neither a voice entry nor an impossible reason"

    for key, reason in VC.VOICE_IMPOSSIBLE.items():
        family = key.split(".")[0]
        assert family in VC.CLI_COVERAGE, key
        assert reason and len(reason) > 20, (key, reason)
        if "." not in key:
            assert VC.CLI_COVERAGE[family] == (), key


# ------------------------------------------------------------- 2. catalog
def test_every_catalog_op_has_a_registry_entry_or_impossible_reason():
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    gaps = []
    for op in OPS.catalog():
        op_id = op["id"]
        if "voice" in op["interfaces"]:
            if _voice_surface(op_id) is None:
                gaps.append((op_id, "voice-entry"))
        else:
            if not op.get("interface_reason"):
                gaps.append((op_id, "interface-reason"))
            if _voice_surface(op_id) is None:
                gaps.append((op_id, "voice-guard"))
    assert not gaps, f"catalog ops with a silent voice gap: {gaps}"

    # every guard is a real registered entry with a real alias
    for op_id in VC.VOICE_GUARDS:
        assert f"guard_{op_id}" in VC.REGISTRY, op_id


def test_ledger_voice_unsupported_rows_are_registered():
    """The 18 typed ``voice_unsupported`` rows resolve to real guard entries."""
    if not LEDGER.is_file():
        pytest.skip("gap ledger not present")
    rows = json.loads(LEDGER.read_text(encoding="utf-8"))["rows"]
    unsupported = [r for r in rows if r["area"] == "voice_unsupported"]
    assert len(unsupported) >= 18, len(unsupported)
    from voicebridge import voice_commands as VC

    for row in unsupported:
        assert row["status"] == "IMPLEMENTED", row["id"]
        assert row["voice"] in VC.REGISTRY, (row["id"], row["voice"])
        assert VC.REGISTRY[row["voice"]].backend == "voice_unsupported"


# --------------------------------------------------------- 3. alias parity
def test_every_alias_resolves_to_its_own_production_entry():
    """No alias may silently route to a different backend than its entry."""
    from voicebridge import voice_commands as VC

    misrouted = []
    for entry in VC.REGISTRY.values():
        for alias in entry.aliases:
            res = VC.resolve("qubit, " + alias)
            got = res.entry.id if res.entry is not None else None
            if res.kind not in (VC.MATCH, VC.CLARIFY) or got != entry.id:
                misrouted.append((entry.id, alias, res.kind, got))
    assert not misrouted, f"aliases not bound to their entry: {misrouted}"


def test_wake_word_does_not_eat_command_words():
    """Regression: 'computer'/'qubit' inside a command must not be stripped as
    a wake word (it used to misroute computer_* and product_status aliases)."""
    from voicebridge import voice_commands as VC

    cases = {
        "qubit, computer status": "computer_status",
        "qubit, computer doctor": "computer_doctor",
        "qubit, computer permissions": "computer_permissions",
        "qubit, enable computer control": "computer_install_refusal",
        "qubit, install status": "product_status",
    }
    for text, want in cases.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == want, (text, res)
    assert VC._norm("qubit, computer status") == "computer status"


def test_read_aliases_share_the_dashboard_backend_result(tmp_path, monkeypatch):
    """Voice and ``run_op`` (the CLI/dashboard dispatcher) reach the same
    backend and return the byte-identical result for representative reads."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for text, entry_id, backend in _READ_BATTERY:
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == entry_id, (text, res)
        entry = VC.REGISTRY[entry_id]
        assert entry.backend == backend, (entry_id, entry.backend, backend)
        assert entry.backend in VC._BACKENDS, entry_id

        said = VC.voice_say(cfg, text, session=f"w-{entry_id}")
        direct = OPS.run_op(cfg, entry_id, {}, actor="voice:user",
                            session=f"w-{entry_id}")
        assert said["state"] == direct["state"] == "executed", (entry_id, said)
        assert said["receipt"]["result"] == direct["receipt"]["result"], entry_id
        assert said.get("confirmation_id") is None, entry_id


# -------------------------------------------------- 4. mutating lifecycle
def test_mutating_ops_confirm_cancel_replay_estop_and_isolation(
        tmp_path, monkeypatch):
    from voicebridge import power as PW
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg, session="mut")

    # Stage exactly one durable confirmation; it is not executed on speak.
    staged = VC.execute(ctx, VC.resolve("qubit, create task wave3"))
    assert staged.kind == VC.NEEDS_CONFIRM, (staged.kind, staged.speech)
    assert staged.confirmation_id
    from voicebridge import approvals as AP

    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    pending = [a for a in journal.pending()
               if a.capability == "voice.kanban_create"]
    assert len(pending) == 1, "exactly one staged confirmation"

    # Session isolation: another session cannot settle this confirmation.
    other = _ctx(cfg, session="other")
    isolated = VC.confirm_pending(other, staged.confirmation_id)
    assert isolated.kind == VC.FAILED, isolated.speech

    # Cancel settles it once; a later confirm is a typed failure, never a
    # silent success.
    cancelled = VC.cancel_pending(ctx, staged.confirmation_id)
    assert cancelled.kind == VC.EXECUTED, cancelled.speech
    gone = VC.confirm_pending(ctx, staged.confirmation_id)
    assert gone.kind == VC.FAILED, gone.speech

    # Replay: a fresh confirmation executes once, then is single-use.
    staged = VC.execute(ctx, VC.resolve("qubit, create task wave3-replay"))
    first = VC.confirm_pending(ctx, staged.confirmation_id)
    assert first.kind == VC.EXECUTED, first.speech
    replay = VC.confirm_pending(ctx, staged.confirmation_id)
    assert replay.kind == VC.FAILED, replay.speech

    # Emergency stop latches mutations and leaves reads available; the
    # e-stop reset path itself stays reachable.
    PW.power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                    issued_by="cli:user")
    try:
        blocked = VC.execute(ctx, VC.resolve("qubit, create task blocked"))
        assert blocked.kind == VC.REFUSED, (blocked.kind, blocked.speech)
        assert "latched" in blocked.speech.lower(), blocked.speech
        assert not blocked.confirmation_id, "a latched e-stop stages nothing"
        allowed = VC.execute(ctx, VC.resolve("qubit, list skills"))
        assert allowed.kind == VC.EXECUTED, allowed.speech
    finally:
        PW.power_control_store(cfg).set(emergency_stop=False,
                                        global_disable=False,
                                        issued_by="cli:user")


# ------------------------------------------------------- 5. no fake success
def test_no_guard_or_staged_write_fabricates_success(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg, session="fake")
    allowed = {"PASSPHRASE_NEVER_SPOKEN", "VOICE_NESTED_UNSUPPORTED",
               "VOICE_PREVIEW_HASH_ONLY", "VOICE_BYTES_UNSUPPORTED"}

    # Guards execute a typed, audited refusal and bind no unsafe payload.
    for op_id, spec in VC.VOICE_GUARDS.items():
        entry_id = f"guard_{op_id}"
        said = VC.voice_say(cfg, "qubit, " + spec["aliases"][0],
                            session=f"g-{op_id}")
        assert said["state"] == "executed", (op_id, said)
        assert said["route"] == entry_id, (op_id, said)
        result = said["receipt"]["result"]
        assert result["state"] == "refused", (op_id, result)
        assert result["code"] == spec["code"] in allowed, (op_id, result)
        assert len(result["guidance"]) > 20, (op_id, result)
        assert not said.get("confirmation_id"), op_id

    # A confirm-tier write over the shared dispatcher stages (409-style),
    # never a fabricated 2xx execution.
    broker = OPS.run_op(cfg, "kanban_create", {"title": "fake"},
                        actor="voice:user", session="fake")
    assert broker["state"] == "needs-confirm", broker
    assert broker.get("confirmation_id"), broker
    OPS.cancel_op(cfg, broker["confirmation_id"], actor="voice:user",
                  session="fake")

    # A gated runtime degrades to a typed state, not a fake OK.
    cfg.data.setdefault("agent", {})["enabled"] = False
    staged = VC.execute(ctx, VC.resolve("qubit, run the task wave3"))
    assert staged.kind == VC.NEEDS_CONFIRM
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, done.speech
    task = done.receipt["result"]["task"]
    assert task["state"] == "UNAVAILABLE", task
    assert task.get("code"), task


def test_non_user_source_never_stages_or_executes(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for source in ("model", "page", "tool", "agent"):
        ctx = _ctx(cfg, session="src", source=source)
        for text in ("qubit, create task nope", "qubit, list skills",
                     "qubit, apply a file patch"):
            out = VC.execute(ctx, VC.resolve(text))
            assert out.kind == VC.REFUSED, (source, text, out.kind)
            assert not out.confirmation_id, (source, text)
            assert out.receipt == {}, (source, text, out.receipt)
