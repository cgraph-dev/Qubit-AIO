"""POSIX pre-swap gateway pause: the unit-level contracts that do not need a live install.

The full killed-updater adoption and the live stop/restart of real gateways are integration shapes
that depend on the pause-record test's child-process harness (not present in this tree); these cover
the pure-logic seams the module owns: the paused prompt shortcut, self-preservation on unknown cgroup
membership, and the replay environment's per-profile isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading

def test_a_prompt_while_the_gateways_are_paused_takes_its_default_at_once(tmp_path, monkeypatch):
    """``/update`` relays prompts through the gateway; while this update holds it stopped nobody can
    answer, so the prompt must not park the update (and the downtime) for its 300 s timeout."""
    from qubit_cli import update_cmd, update_cmd_posix_pause

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    monkeypatch.setattr(update_cmd_posix_pause, "_RUN",
                        {"platform": "posix", "posix_stopped": True, "resume_needed": True})
    got: list[str] = []
    t = threading.Thread(target=lambda: got.append(update_cmd._gateway_prompt("Restore local changes now?", "n")),
                         daemon=True)
    t.start()
    t.join(timeout=10)
    assert got == ["n"], "a prompt waited for a gateway this update had stopped"
    assert not (tmp_path / ".update_prompt.json").exists()


def test_unknown_cgroup_membership_never_authorizes_stopping_the_unit():
    """A unit whose stop could kill this update stays running unless the update is PROVEN outside
    its cgroup; unreadable membership is unknown, never outside."""
    from qubit_cli.update_cmd_posix_pause import _escape_cgroup
    assert _escape_cgroup({"unit": "hermes-gateway-p2probe.service", "pid": 4242, "cgroup": None}) is False


def test_a_replayed_gateway_keeps_its_own_exported_token_and_no_one_elses(tmp_path):
    """A named profile's hand-started gateway, replayed by an update launched under that profile,
    starts as the restart watcher started it: with the launcher's environment (an exported bot
    token may be its only credential). Any other profile, and the multiplexing root, never get it."""
    root = tmp_path / ".qubit"
    for name in ("alpha", "beta"):
        (root / "profiles" / name).mkdir(parents=True)
    code = textwrap.dedent("""
        import json, sys
        from qubit_cli.update_cmd_posix_pause import _replay_env
        root = sys.argv[1]
        print(json.dumps({who: _replay_env(home).get("TELEGRAM_BOT_TOKEN") for who, home in (
            ("alpha", root + "/profiles/alpha"), ("beta", root + "/profiles/beta"), ("root", root))}))
    """)
    env = {**os.environ, "HOME": str(tmp_path), "QUBIT_HOME": str(root / "profiles" / "alpha"),
           "TELEGRAM_BOT_TOKEN": "exported-alpha-token",
           "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    out = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, text=True,
                         timeout=60, env=env, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got == {"alpha": "exported-alpha-token", "beta": None, "root": None}, got


def test_public_surface_is_present():
    """The wired names the update command needs are importable off the module."""
    from qubit_cli import update_cmd_posix_pause as m
    for name in ("arm_pause", "gateways_paused", "pause_at_commit_point", "resume_paused_set",
                 "already_restarted", "PauseRefused"):
        assert hasattr(m, name), name
    assert m._empty_token()["platform"] == "posix"
    assert m.already_restarted() == {"units": set(), "labels": set(), "pids": set()}
