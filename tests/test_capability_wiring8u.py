"""Packet 8U — capability wiring: node host / task ledger / media / local models.

Proves the four new backend modules are integrated into all three shared
surfaces with one backend operation each:

* voice: the registry entry resolves its example and executes (or returns the
  module's typed gate) through the shared confirmation pipeline;
* CLI: the direct family parses and ``main`` dispatches to the same module;
* dashboard: the Tier-1 catalog exposes every registry entry and ``run_op``
  executes the same backend.

No skips, no xfail: every assertion runs real code against hermetic state.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p), p


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


# Registry ids that must be wired for each module (voice + dashboard Tier-1).
MODULE_ENTRIES = {
    "node_host": (
        "node_host_status", "node_host_list", "node_host_show",
        "node_host_capabilities", "node_host_enroll", "node_host_revoke",
        "node_host_dispatch",
    ),
    "task_ledger": (
        "task_ledger_status", "task_ledger_list", "task_ledger_show",
        "task_ledger_timeline",
    ),
    "media_gen": ("media_gen_status", "media_gen_catalog", "media_gen_generate"),
    "local_models": (
        "local_models_status", "local_models_hardware", "local_models_catalog",
        "local_models_jobs", "local_models_runtime_install",
        "local_models_download", "local_models_sideload", "local_models_server",
        "local_models_eject", "local_models_activate", "local_models_quickstart",
    ),
}


# --------------------------------------------------------------------- voice
def test_voice_reads_execute_for_every_module(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    reads = {
        "qubit, node host status": "node_host_status",
        "qubit, list nodes": "node_host_list",
        "qubit, task ledger status": "task_ledger_status",
        "qubit, list background tasks": "task_ledger_list",
        "qubit, media status": "media_gen_status",
        "qubit, media catalog": "media_gen_catalog",
        "qubit, local models status": "local_models_status",
        "qubit, local model hardware": "local_models_hardware",
        "qubit, local model catalog": "local_models_catalog",
        "qubit, local model jobs": "local_models_jobs",
    }
    for text, entry_id in reads.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == entry_id, (text, res)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
        assert out.receipt.get("result"), text


def test_node_host_voice_enroll_dispatch_and_revoke(tmp_path, monkeypatch):
    from voicebridge import node_host as NH
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)

    staged = VC.execute(
        ctx, VC.resolve("qubit, enroll node desk http://127.0.0.1:8443/"))
    assert staged.kind == VC.NEEDS_CONFIRM, (staged.kind, staged.speech)
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, (done.kind, done.speech)
    node = done.receipt["result"]["node_host"]["node"]
    nid = node["id"]
    assert nid.startswith("node_")

    # declared read capability dispatches and executes
    NH.enroll(cfg, name="desk", endpoint="http://127.0.0.1:8443/",
              capabilities=["device.inventory"], confirmed=True)
    nid2 = NH.list_nodes(cfg)["nodes"][0]["id"]
    out = VC.execute(ctx, VC.resolve(
        f"qubit, dispatch node capability {nid2} device.inventory"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["node_host"]["state"] == "OK"

    # remote/unsafe capability is the module's typed refusal, never a shell
    out = VC.execute(ctx, VC.resolve(
        f"qubit, dispatch node capability {nid2} screen"))
    assert out.kind == VC.EXECUTED
    assert out.receipt["result"]["node_host"]["code"] == NH.NODE_DISPATCH_REFUSED

    caps = VC.execute(ctx, VC.resolve(f"qubit, node capabilities {nid2}"))
    assert caps.kind == VC.EXECUTED
    assert caps.receipt["result"]["node_host"]["state"] == "OK"

    rev = VC.execute(ctx, VC.resolve(f"qubit, revoke node {nid}"))
    assert rev.kind == VC.NEEDS_CONFIRM
    rev_done = VC.confirm_pending(ctx, rev.confirmation_id)
    assert rev_done.kind == VC.EXECUTED
    assert rev_done.receipt["result"]["node_host"]["removed"] == nid


def test_media_generate_is_confirmed_and_returns_typed_gate(tmp_path,
                                                            monkeypatch):
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    staged = VC.execute(ctx, VC.resolve("qubit, generate image a red fox"))
    assert staged.kind == VC.NEEDS_CONFIRM, (staged.kind, staged.speech)
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, (done.kind, done.speech)
    media = done.receipt["result"]["media_gen"]
    assert media["state"] == "FAILED"
    assert media["code"].startswith("MEDIA_")


def test_local_model_write_verbs_are_typed_gates(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    gates = {
        "qubit, install local runtime": "LOCAL_MODEL_",
        "qubit, download local model unknown-model": "LOCAL_MODEL_",
        "qubit, sideload local model /tmp/model.gguf": "LOCAL_MODEL_",
        "qubit, start local model server": "LOCAL_MODEL_",
        "qubit, eject local model unknown-model": "LOCAL_MODEL_",
        "qubit, activate local model unknown-model": "LOCAL_MODEL_",
        "qubit, local model quickstart": "LOCAL_MODEL_",
        "qubit, local model jobs": None,
    }
    for text, prefix in gates.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
        result = out.receipt["result"]["local_models"]
        assert result.get("state") in ("REFUSED", "EMPTY", "UNAVAILABLE",
                                       "DEGRADED"), (text, result)
        if prefix is not None:
            assert str(result.get("code", "")).startswith(prefix), (text,
                                                                    result)


# ----------------------------------------------------------------------- CLI
def test_cli_families_parse(tmp_path, monkeypatch):
    import argparse

    from voicebridge.cli import build_parser

    _cfg(tmp_path, monkeypatch)
    parser = build_parser()
    top = next(a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction))
    assert {"node", "tasks", "media", "local-models"} <= set(top.choices)
    for family in ("node", "tasks", "media", "local-models"):
        sub = top.choices[family]
        subs = next(a for a in sub._actions
                    if isinstance(a, argparse._SubParsersAction))
        if family == "node":
            assert {"status", "list", "show", "capabilities", "register",
                    "revoke", "dispatch"} <= set(subs.choices)
        elif family == "tasks":
            assert {"status", "list", "show", "timeline"} <= set(subs.choices)
        elif family == "media":
            assert {"status", "catalog", "generate"} <= set(subs.choices)
        else:
            assert {"status", "hardware", "catalog", "jobs",
                    "runtime-install", "download", "sideload", "server",
                    "eject", "activate", "quickstart"} <= set(subs.choices)


@pytest.mark.parametrize("argv,key", [
    (["node", "status"], "node_ids"),
    (["tasks", "status"], "total"),
    (["media", "status"], "providers"),
    (["local-models", "status"], "provider"),
])
def test_cli_dispatch_prints_typed_json(tmp_path, monkeypatch, argv, key):
    from voicebridge import cli as CLI

    _cfg(tmp_path, monkeypatch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(argv + ["--config", str(tmp_path / "voice.yaml")])
    assert rc == 0, buf.getvalue()
    body = json.loads(buf.getvalue())
    assert key in body, (argv, sorted(body))


def test_cli_node_register_and_revoke(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import node_host as NH

    cfg, _ = _cfg(tmp_path, monkeypatch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([
            "node", "register", "--name", "desk",
            "--endpoint", "http://127.0.0.1:8443/",
            "--capability", "device.inventory", "--confirm",
            "--config", str(tmp_path / "voice.yaml")])
    assert rc == 0, buf.getvalue()
    node = json.loads(buf.getvalue())["node"]
    assert node["capabilities"] == ["device.inventory"]

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([
            "node", "revoke", node["id"], "--confirm",
            "--config", str(tmp_path / "voice.yaml")])
    assert rc == 0, buf.getvalue()
    assert json.loads(buf.getvalue())["removed"] == node["id"]
    assert NH.list_nodes(cfg)["nodes"] == []


def test_cli_local_model_gate_is_typed(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    _cfg(tmp_path, monkeypatch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([
            "local-models", "download", "unknown-model",
            "--config", str(tmp_path / "voice.yaml")])
    assert rc == 0, buf.getvalue()
    body = json.loads(buf.getvalue())
    assert body["state"] == "REFUSED"
    assert body["code"].startswith("LOCAL_MODEL_")


# ----------------------------------------------------------------- dashboard
def test_dashboard_catalog_exposes_every_entry_tier1(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    _cfg(tmp_path, monkeypatch)
    cat = {item["id"]: item for item in OPS.catalog()}
    for module, ids in MODULE_ENTRIES.items():
        for entry_id in ids:
            assert entry_id in cat, (module, entry_id)
            item = cat[entry_id]
            assert item["tier"] == "registry", entry_id
            assert item["group"] == module, (entry_id, item["group"])
            assert item["interfaces"] == ["cli", "dashboard", "voice"], entry_id
            assert item["help"], entry_id


@pytest.mark.parametrize("op_id", [
    "node_host_status", "task_ledger_status", "media_gen_catalog",
    "local_models_status", "local_models_jobs",
])
def test_dashboard_runs_same_backend(tmp_path, monkeypatch, op_id):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    def _scrub(value):
        if isinstance(value, dict):
            return {k: _scrub(v) for k, v in value.items()
                    if not (k in ("generated_at", "free_vram_mib")
                            or k.endswith(("_at", "_ms")))}
        if isinstance(value, list):
            return [_scrub(v) for v in value]
        return value

    cfg, _ = _cfg(tmp_path, monkeypatch)
    out = OPS.run_op(cfg, op_id, {}, actor="dashboard:user",
                     session="dashboard")
    assert out["state"] == "executed", (op_id, out)
    assert out["receipt"]["entry"] == op_id

    entry = VC.REGISTRY[op_id]
    expected = VC._BACKENDS[entry.backend](
        VC.make_ctx(cfg, actor="probe", session="probe", source="cli"),
        dict(entry.fixed or ()))
    assert _scrub(out["receipt"]["result"]) == _scrub(expected)
