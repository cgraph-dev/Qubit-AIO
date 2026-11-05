"""Startup fast-path regressions (fix7/start).

Covers the two measured hot spots:

* ``qubit --version`` must not build the ~870-parser argparse tree.
* ``GET /api/health`` must not build the full runtime/status inventory on the
  unauthenticated liveness route, and its installation-health STATE is served
  through a short-TTL snapshot (with an env kill-switch).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "q"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


@pytest.fixture(autouse=True)
def _clear_health_cache():
    from voicebridge import qubit_compat as HC

    HC._HEALTH_STATE_CACHE.clear()
    yield
    HC._HEALTH_STATE_CACHE.clear()


# ---------------------------------------------------------------------------
# /api/health liveness
# ---------------------------------------------------------------------------
def test_health_does_not_build_full_status_or_runtime_inventory(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import lifecycle as LC

    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(*_a, **_k):
        raise AssertionError("h_status must not run on the liveness route")

    monkeypatch.setattr(HC, "h_status", _boom)
    calls = []
    monkeypatch.setattr(LC, "health_inventory",
                        lambda _cfg: calls.append(1) or {"state": "READY"})

    code, out = HC.h_health(cfg, {}, {}, {})

    assert code == 200, out
    assert out["ok"] is True
    assert out["version"].startswith("qubit-")
    assert isinstance(out["auth_required"], bool)
    assert out["state"] == "READY"
    assert calls == [1]


def test_health_state_is_cached_within_ttl(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import lifecycle as LC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("QUBIT_HEALTH_STATE_TTL_S", "60")
    calls = []
    monkeypatch.setattr(LC, "health_inventory",
                        lambda _cfg: calls.append(1) or {"state": "DEGRADED"})

    first = HC.h_health(cfg, {}, {}, {})[1]
    second = HC.h_health(cfg, {}, {}, {})[1]

    assert first["state"] == "DEGRADED" == second["state"]
    assert len(calls) == 1, calls


def test_health_state_ttl_zero_disables_the_cache(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import lifecycle as LC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("QUBIT_HEALTH_STATE_TTL_S", "0")
    calls = []
    monkeypatch.setattr(LC, "health_inventory",
                        lambda _cfg: calls.append(1) or {"state": "READY"})

    HC.h_health(cfg, {}, {}, {})
    HC.h_health(cfg, {}, {}, {})

    assert len(calls) == 2, calls


def test_health_state_cache_key_tracks_config_mtime(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import lifecycle as LC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("QUBIT_HEALTH_STATE_TTL_S", "60")
    calls = []
    monkeypatch.setattr(LC, "health_inventory",
                        lambda _cfg: calls.append(1) or {"state": "READY"})

    HC.h_health(cfg, {}, {}, {})
    # Rewrite the config file: a stale cached STATE must not outlive the change.
    path = Path(cfg.path)
    path.write_text("logging:\n  jsonl: false\n# changed\n")
    HC.h_health(cfg, {}, {}, {})

    assert len(calls) == 2, calls


def test_health_auth_required_matches_the_status_gate(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import lifecycle as LC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(LC, "health_inventory", lambda _c: {"state": "READY"})

    for token in ("", "sekret"):
        cfg.data.setdefault("webui", {})["token"] = token
        expected = HC._status_auth_gate(cfg)["auth_required"]
        got = HC.h_health(cfg, {}, {}, {})[1]["auth_required"]
        assert got is expected, (token, got, expected)


# ---------------------------------------------------------------------------
# `qubit --version` fast path
# ---------------------------------------------------------------------------
def test_version_short_circuits_before_building_the_parser(
        monkeypatch, capsys):
    import voicebridge.cli as CLI
    from voicebridge import product as P

    monkeypatch.setattr(sys, "argv", ["qubit"])
    monkeypatch.setattr(
        CLI, "build_parser",
        lambda: (_ for _ in ()).throw(
            AssertionError("build_parser must not run for --version")))

    rc = CLI.main(["--version"])
    out = capsys.readouterr().out.strip()

    assert rc == 0
    assert out == f"qubit {P.PRODUCT_VERSION}"


def test_version_output_matches_the_parser_action(monkeypatch, capsys):
    import voicebridge.cli as CLI
    from voicebridge import product as P

    monkeypatch.setattr(sys, "argv", ["qubit"])
    parser = CLI.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"qubit {P.PRODUCT_VERSION}"


def test_agent_version_still_forwards_to_the_vendored_runtime(monkeypatch):
    import voicebridge.cli as CLI
    import voicebridge.qubit_runtime as QR

    seen = {}

    def _run(argv):
        seen["argv"] = list(argv)
        return 0

    monkeypatch.setattr(QR, "run", _run)

    assert CLI.main(["agent", "--version"]) == 0
    assert seen["argv"] == ["--version"], seen


# ---------------------------------------------------------------------------
# gettext lookup memoisation (argparse help construction)
# ---------------------------------------------------------------------------
def test_gettext_find_cache_is_idempotent_and_keeps_help_stable():
    import gettext

    import voicebridge.cli as CLI

    before = CLI.build_parser().format_help()
    CLI._install_gettext_find_cache()
    first = gettext.find
    assert getattr(first, "_qubit_cached", False)
    CLI._install_gettext_find_cache()
    assert gettext.find is first

    after = CLI.build_parser().format_help()
    assert after == before
    # The original callable is retained for diagnostics/tests.
    assert callable(getattr(first, "_qubit_orig", None))
