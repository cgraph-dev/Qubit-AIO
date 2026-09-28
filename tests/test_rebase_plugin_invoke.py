"""Rebase gate — the in-process plugin invoke surface survives v0.21.5.

The 2026-09-27 catalog verification reported ``builtin.echo`` ABSENT and no
``qubit plugins invoke --confirm`` surface at this HEAD. Investigation shows
the opposite: the Qubit-native in-process runtime
(:mod:`voicebridge.plugin_runtime`) and its confirm-gated CLI
(``voicebridge.cli`` -> ``_cmd_plugins``) are present and real. The vendored
``qubit agent plugins`` surface (install/search/browse/validate/adopt/trust/...)
never owned ``invoke``, so the two surfaces are not the same command family.

These tests pin that rebase invariant end to end, so a future vendor sync that
drops the Qubit overlay fails loudly instead of silently degrading to the
"described-but-absent" state:

* the fork-forward router must NOT shadow ``qubit plugins invoke``;
* an unconfirmed invoke runs nothing (typed ``PLUGIN_CONFIRM_REQUIRED``);
* a confirmed ``builtin.echo`` invoke runs the real in-process handler and
  writes a real ``plugin.invoke`` receipt (id + ``args_sha256``) to the event
  journal;
* a registered manifest whose entrypoint has no host handler resolves to the
  typed ``PLUGIN_RUNTIME_UNAVAILABLE`` gate — never a fabricated success;
* the ``builtin.achievements`` host adapter over the canonical backend is still
  registered and invocable.

Hermetic: temp XDG dirs + temp config; no network, no subprocess.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ECHO = "builtin.echo"
ACHIEVEMENTS = "builtin.achievements"
HANDLERLESS = "acme.widget"
DIGEST = "a" * 64


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: true\n")
    return C.Config.load(p)


def _events(cfg):
    path = cfg.state_dir() / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _run(argv, cfg_path):
    from voicebridge import cli

    return cli.main(["--config", str(cfg_path), *argv])


def _manifest(pid: str = HANDLERLESS):
    return {"id": pid, "kind": "tool", "entrypoint": "acme.plugin:main",
            "digest": DIGEST, "label": "Acme Widget", "version": "1.0.0",
            "capabilities": ["run"]}


# ---------------------------------------------------------------------------
# The native surface owns the call; the fork-forward router must not shadow it
# ---------------------------------------------------------------------------

def test_forward_router_does_not_shadow_native_plugins_invoke():
    from voicebridge import cli
    from voicebridge import cli_qubit_forward as FWD

    argv = ["plugins", "invoke", ECHO, "--text", "x", "--confirm"]
    assert FWD.route(list(argv)) is None, (
        "qubit plugins invoke must stay Qubit-native, never fork-forwarded")
    args = cli.build_parser().parse_args(argv)
    assert args.command == "plugins"
    assert args.plugins_command == "invoke"
    assert args.plugin_id == ECHO
    assert args.text == "x"
    assert args.confirm is True


# ---------------------------------------------------------------------------
# Confirm gate: no --confirm runs nothing and emits no receipt
# ---------------------------------------------------------------------------

def test_cli_invoke_requires_confirm_and_runs_nothing(tmp_path, monkeypatch,
                                                      capsys):
    cfg = _cfg(tmp_path, monkeypatch)

    rc = _run(["plugins", "invoke", ECHO, "--text", "must-not-run"],
              tmp_path / "voice.yaml")
    out = json.loads(capsys.readouterr().out)

    assert rc == 3
    assert out["state"] == "denied"
    assert out["code"] == "PLUGIN_CONFIRM_REQUIRED"
    assert not [e for e in _events(cfg) if e.get("event") == "plugin.invoke"], \
        "an unconfirmed invoke must leave no plugin.invoke receipt"


# ---------------------------------------------------------------------------
# Confirmed invoke: the real handler runs and the journal carries the receipt
# ---------------------------------------------------------------------------

def test_cli_invoke_echo_writes_real_invocation_receipt(tmp_path, monkeypatch,
                                                        capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    text = "rebase-probe"

    rc = _run(["plugins", "invoke", ECHO, "--text", text, "--confirm"],
              tmp_path / "voice.yaml")
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["state"] == "OK"
    assert out["plugin"] == ECHO
    assert out["result"] == {"echo": text, "handler": ECHO}, \
        "the real in-process echo handler must have run"

    receipts = [e for e in _events(cfg) if e.get("event") == "plugin.invoke"]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["id"] == ECHO
    expected = hashlib.sha256(
        json.dumps({"text": text}, default=repr).encode()).hexdigest()
    assert receipt["args_sha256"] == expected, \
        "the receipt must be bound to the exact invoked arguments"


# ---------------------------------------------------------------------------
# A handlerless manifest entry is a typed UNAVAILABLE, never fake success
# ---------------------------------------------------------------------------

def test_registered_entry_without_handler_is_typed_unavailable(
        tmp_path, monkeypatch, capsys):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)

    reg = PR.register(cfg, manifest=_manifest(), confirmed=True,
                      requested_by="cli:user")
    assert reg["plugin"]["handler"] is False

    with pytest.raises(PR.PluginRuntimeError) as ei:
        PR.invoke(cfg, HANDLERLESS, confirmed=True, requested_by="cli:user")
    assert ei.value.code == PR.UNAVAILABLE_CODE

    rc = _run(["plugins", "invoke", HANDLERLESS, "--confirm"],
              tmp_path / "voice.yaml")
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["state"] == "FAILED"
    assert out["code"] == "PLUGIN_RUNTIME_UNAVAILABLE"
    assert "result" not in out and out.get("state") != "OK", \
        "a manifest entrypoint must never report a fabricated success"

    assert not [e for e in _events(cfg)
                if e.get("event") == "plugin.invoke"
                and e.get("id") == HANDLERLESS], \
        "nothing ran, so no invocation receipt may exist for the entry"


# ---------------------------------------------------------------------------
# builtin.achievements host adapter over the canonical backend survives
# ---------------------------------------------------------------------------

def test_builtin_achievements_host_adapter_still_real(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)

    caps = PR.host_capabilities(cfg)
    rows = {row["id"]: row for row in caps["capabilities"]}
    assert ACHIEVEMENTS in rows
    assert rows[ACHIEVEMENTS]["handler"] is True
    assert "builtin.echo" in PR.status(cfg)["builtin"]
    assert PR.status(cfg)["handler_kinds"].get(ACHIEVEMENTS) == "tool"

    out = PR.invoke(cfg, ACHIEVEMENTS, args={"action": "status"},
                    confirmed=True, requested_by="cli:user")
    assert out["state"] == "OK"
    assert isinstance(out["result"], dict) and out["result"], \
        "the achievements adapter must return its real backend payload"