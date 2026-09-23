"""Dashboard-audit truthfulness fixes (2026-09-23).

Four audit items, each proven through the production surface:

* gateway ``_pin_toolset_rows`` (``tools.catalog`` / ``tools.effective``) must
  agree with the dashboard read path for platform-disabled toolsets instead of
  defaulting an unrecorded row to enabled.
* a confirmed extra-op failure/refusal must not be reported as ``executed``;
  the confirm envelope carries the true receipt state (``ok:false``).
* a staged ``skill.mutation`` must be cancellable through the one cancel path
  (dashboard_ops.cancel_op / POST /api/ops/cancel).
* MoA defaults must not fabricate providers Qubit does not have.

Hermetic: tmp XDG state only; no network, no live services.
"""
from __future__ import annotations

import hashlib


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _manifest(content: str, skill_id: str = "cancel-me") -> dict:
    return {
        "skill_id": skill_id, "name": skill_id, "version": "1.0.0",
        "description": "demo skill", "source": "qubit-dashboard",
        "revision": "1", "license": "MIT",
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "capabilities": ["skill.demo"], "scopes": ["skill:read"],
        "tier": "reversible", "rollback": "remove the skill",
    }


# --------------------------------------------------- gateway toolset authority
def test_pinned_gateway_catalog_agrees_with_read_path(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import toolsets as TS
    from voicebridge.gateway import rpc

    cfg = _cfg(tmp_path, monkeypatch)
    read = {r["name"]: r["enabled"] for r in QC.h_toolsets(cfg, {}, {}, {})[1]}
    disabled = [n for n, on in read.items() if not on]
    assert disabled, "expected at least one platform-disabled toolset"
    name = disabled[0]

    pinned = {r["name"]: r["enabled"] for r in rpc._pin_toolset_rows(cfg)}
    assert pinned[name] is False
    assert pinned[name] == TS.effective_toolset_enabled(cfg, name)

    # tools.effective is the enabled subset of the pinned catalog.
    effective = rpc._pinned_tools_effective(cfg, {})
    assert name not in {r["name"] for r in effective["tools"]}

    # A persisted enable makes the read path and the pinned catalog agree it is
    # on (the old default-to-enabled branch reported the mirror image).
    TS.apply_toolset_mutation(cfg, name, True, actor="dashboard:user")
    pinned = {r["name"]: r["enabled"] for r in rpc._pin_toolset_rows(cfg)}
    assert pinned[name] is True


# --------------------------------------------------- truthful op envelopes
def test_confirmed_refused_mutation_is_not_reported_executed(tmp_path,
                                                             monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.qubit_compat import _confirm_payload

    cfg = _cfg(tmp_path, monkeypatch)
    staged = OPS.run_op(cfg, "rollback_model_profile",
                        {"profile_id": "qubit-brain4", "session": "s1"},
                        actor="dashboard:user", session="dashboard")
    assert staged["state"] == "needs-confirm", staged

    done = OPS.confirm_op(cfg, staged["confirmation_id"],
                          actor="dashboard:user", session="dashboard")
    assert done["state"] in ("refused", "failed"), done
    assert done["code"] == "NO_ROLLBACK", done
    # The true receipt state is preserved for the detail view.
    assert done["receipt"]["result"]["state"] == "REFUSED", done

    http, payload = _confirm_payload(done)
    assert http == 400, (http, payload)
    assert payload["ok"] is False, payload


def test_unconfirmed_read_failure_is_not_reported_executed(tmp_path,
                                                           monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, "test_model_profile", {"profile_id": "no-such"},
                     actor="dashboard:user", session="dashboard")
    assert out["state"] == "failed", out
    assert out["code"] == "UNKNOWN_PROFILE", out
    assert out["receipt"]["result"]["state"] == "FAILED", out


def test_dedicated_refusal_ops_still_execute_typed(tmp_path, monkeypatch):
    """The unconfirmed refusal operations legitimately deliver a typed result."""
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, "proxy_start", {}, actor="dashboard:user",
                     session="dashboard")
    assert out["state"] == "executed", out
    assert out["receipt"]["result"]["code"] == "PROXY_NO_SERVER", out


# --------------------------------------------------- skill-mutation cancel
def test_staged_skill_mutation_is_cancellable(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    content = "# Demo\n\nDo the thing.\n"
    staged = V.stage_skill_mutation(cfg, "install",
                                    {"manifest": _manifest(content)},
                                    actor="dashboard:user",
                                    session="dashboard")
    assert staged["state"] == "needs-confirm", staged
    cid = staged["confirmation_id"]
    assert [p["id"] for p in V.pending_skill_mutations(cfg)] == [cid]

    cancelled = OPS.cancel_op(cfg, cid, actor="dashboard:user",
                              session="dashboard")
    assert cancelled["state"] == "executed", cancelled
    assert cancelled["receipt"]["cancelled"] == 1
    assert V.pending_skill_mutations(cfg) == []

    # settle-after-cancel is typed, never applies
    replay = V.settle_skill_mutation(cfg, cid)
    assert replay["ok"] is False


def test_ops_cancel_route_settles_skill_journal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    content = "# Demo\n\nDo the thing.\n"
    staged = V.stage_skill_mutation(cfg, "install",
                                    {"manifest": _manifest(content)},
                                    actor="dashboard:user",
                                    session="dashboard")
    cid = staged["confirmation_id"]

    code, out = QC.h_ops_cancel(cfg, {}, {"confirmation_id": cid}, {})
    assert code == 200, out
    assert out["state"] == "executed"
    assert out["receipt"]["cancelled"] == 1
    assert V.pending_skill_mutations(cfg) == []


def test_cancel_all_includes_skill_pending(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    content = "# Demo\n\nDo the thing.\n"
    staged = V.stage_skill_mutation(cfg, "install",
                                    {"manifest": _manifest(content)},
                                    actor="dashboard:user",
                                    session="dashboard")
    assert staged["confirmation_id"]

    cancelled = OPS.cancel_op(cfg, None, actor="dashboard:user",
                              session="dashboard")
    assert cancelled["state"] == "executed", cancelled
    assert cancelled["receipt"]["cancelled"] >= 1
    assert V.pending_skill_mutations(cfg) == []


# --------------------------------------------------- honest MoA defaults
def test_moa_defaults_do_not_fabricate_providers(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    qubit_runtime.ensure_path()
    from qubit_cli.moa_config import (DEFAULT_MOA_AGGREGATOR,
                                      DEFAULT_MOA_REFERENCE_MODELS,
                                      normalize_moa_config)

    assert DEFAULT_MOA_REFERENCE_MODELS == []
    assert DEFAULT_MOA_AGGREGATOR == {}

    cfg = normalize_moa_config({})
    assert cfg["reference_models"] == []
    assert cfg["aggregator"] == {}
    # No fabricated provider/model anywhere in the rendered payload.
    assert "openai-codex" not in str(cfg)
    assert "openrouter" not in str(cfg)
