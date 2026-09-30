"""Dashboard-audit follow-up: pinned catalog name set + cancel truthfulness.

* ``gateway.rpc._pin_toolset_rows`` must render the SAME catalog the dashboard
  read path serves (``qubit_compat.h_toolsets``): the earlier implementation
  read the ``tools`` op *envelope* (which carries no ``names``), so the pinned
  gateway catalog silently degraded to the (empty) persisted-state set.
* ``dashboard_ops.cancel_op`` must answer truthfully for an unknown id and for
  an id owned by another session -- never ``executed``.

Hermetic: tmp XDG state only; no network, no live services.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _read_rows(cfg):
    from voicebridge import qubit_compat as QC

    _code, rows = QC.h_toolsets(cfg, {}, {}, {})
    return {row["name"]: row["enabled"] for row in rows}


def test_pinned_catalog_name_set_matches_read_path(tmp_path, monkeypatch):
    from voicebridge.gateway import rpc

    cfg = _cfg(tmp_path, monkeypatch)
    read = _read_rows(cfg)
    assert read, "expected the vendored toolset catalog to resolve"

    pinned = {r["name"]: r["enabled"] for r in rpc._pin_toolset_rows(cfg)}
    # Full set agreement, not just one row: the envelope-extraction bug left
    # this empty.
    assert set(pinned) == set(read)
    assert pinned == read


def test_pinned_catalog_reflects_persisted_override(tmp_path, monkeypatch):
    from voicebridge import toolsets as TS
    from voicebridge.gateway import rpc

    cfg = _cfg(tmp_path, monkeypatch)
    name = next(n for n, on in _read_rows(cfg).items() if not on)

    TS.apply_toolset_mutation(cfg, name, True, actor="dashboard:user")
    pinned = {r["name"]: r["enabled"] for r in rpc._pin_toolset_rows(cfg)}
    assert pinned[name] is True
    assert pinned[name] == _read_rows(cfg)[name]


def test_cancel_unknown_id_is_typed_not_executed(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    out = OPS.cancel_op(cfg, "no-such-confirmation", actor="dashboard:user",
                        session="dashboard")
    assert out["state"] == "failed", out
    assert out["code"] == "NOTHING_PENDING", out


def test_cancel_other_session_leaves_entry_pending(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import toolsets as TS

    cfg = _cfg(tmp_path, monkeypatch)
    staged = TS.stage_toolset_mutation(cfg, "web", False,
                                       actor="dashboard:user",
                                       session="other-session")
    cid = staged["confirmation_id"]

    out = OPS.cancel_op(cfg, cid, actor="dashboard:user", session="dashboard")
    assert out["state"] == "failed", out
    # The other session's pending entry is untouched.
    assert cid in [p["id"] for p in TS.pending_toolset_mutations(cfg)]

    # The owning session can still cancel it (and it never applies).
    ok = OPS.cancel_op(cfg, cid, actor="dashboard:user", session="other-session")
    assert ok["state"] == "executed", ok
    assert TS.toolset_enabled(cfg, "web") is True
