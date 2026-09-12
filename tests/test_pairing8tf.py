"""Packet 8T-F — pairing kernel: hashed one-time codes + real grants.

Proves codes are shown once and stored hashed, approval issues a scoped
broker grant and consumes the code, failures are typed, revocation
revokes the grant, and the e-stop latches mutations.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _create(cfg, label="owner-phone", scopes=("messaging",), **kw):
    from voicebridge import pairing as PA

    kw.setdefault("confirmed", True)
    return PA.create(cfg, label, list(scopes), **kw)


def test_create_shows_once_stores_hashed(tmp_path, monkeypatch):
    import json as _json

    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    out = _create(cfg)
    code = out["code"]
    assert len(code) == 8
    raw = _json.loads((__import__("pathlib").Path(cfg.state_dir()) /
                       "pairing.json").read_text())
    rec = raw["codes"][out["code_id"]]
    assert "verifier" in rec and "salt_b64" in rec
    assert code not in _json.dumps(raw), "code never stored"
    listed = PA.list_codes(cfg)["codes"][0]
    assert "verifier" not in listed and "code" not in listed


def test_create_requires_confirm_and_validates(tmp_path, monkeypatch):
    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    try:
        PA.create(cfg, "x", ["messaging"], confirmed=False)
    except PA.PairingError as exc:
        assert exc.code == "PAIRING_CONFIRM_REQUIRED"
    else:
        raise AssertionError("confirm required")
    try:
        PA.create(cfg, "x", [], confirmed=True)
    except PA.PairingError as exc:
        assert exc.code == "PAIRING_BAD_SCOPE"
    else:
        raise AssertionError("scopes required")


def test_approve_issues_grant_and_consumes(tmp_path, monkeypatch):
    from voicebridge import pairing as PA
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    out = _create(cfg, account="team")
    approved = PA.approve(cfg, out["code"], confirmed=True)
    assert approved["scopes"] == ["messaging"]
    assert approved["account"] == "team"
    grants = permissions_store(cfg).grants()
    assert any(g.id == approved["grant_id"] for g in grants), \
        "approval confers a real broker grant"
    try:
        PA.approve(cfg, out["code"], confirmed=True)
    except PA.PairingError as exc:
        assert exc.code == "PAIRING_BAD_CODE", "single-use enforced"
    else:
        raise AssertionError("reuse must fail")


def test_wrong_expired_revoked_codes_typed(tmp_path, monkeypatch):
    import json as _json

    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    out = _create(cfg)
    try:
        PA.approve(cfg, "ZZZZZZZZ", confirmed=True)
    except PA.PairingError as exc:
        assert exc.code == "PAIRING_BAD_CODE"
    else:
        raise AssertionError("unknown code must fail")
    path = __import__("pathlib").Path(cfg.state_dir()) / "pairing.json"
    state = _json.loads(path.read_text())
    state["codes"][out["code_id"]]["expires_at"] = 1.0
    path.write_text(_json.dumps(state))
    try:
        PA.approve(cfg, out["code"], confirmed=True)
    except PA.PairingError as exc:
        assert exc.code == "PAIRING_EXPIRED"
    else:
        raise AssertionError("expiry enforced")
    out2 = _create(cfg, label="second")
    res = PA.revoke(cfg, out2["code_id"], confirmed=True)
    assert res["revoked"] == out2["code_id"]
    assert res["grant_gone"] is True, "no grant existed yet"
    remaining = PA.list_codes(cfg)["codes"]
    assert any(r["revoked"] for r in remaining)


def test_revoke_removes_grant(tmp_path, monkeypatch):
    from voicebridge import pairing as PA
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    out = _create(cfg)
    approved = PA.approve(cfg, out["code"], confirmed=True)
    res = PA.revoke(cfg, approved["approved"], confirmed=True)
    assert res["revoked"] == approved["approved"]
    assert res["grant_gone"] is False, "live grant revoked"
    assert all(g.id != approved["grant_id"]
               for g in permissions_store(cfg).grants())


def test_estop_gate(tmp_path, monkeypatch):
    from voicebridge import pairing as PA
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        assert PA.estop_engaged(cfg) is True
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")


def test_cli_wiring(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, cmd, **kw):
            self.pairing_command = cmd
            self.__dict__.update(kw)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_pairing(cfg, _A("list")) == 0
    assert json.loads(buf.getvalue())["codes"] == []
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_pairing(cfg, _A("create", label="x",
                                      scope=["messaging"], account="",
                                      ttl=600.0))
    assert rc == 3, "confirm required"


def _run_integration(cfg, **kw):
    from voicebridge import cli as CLI

    args = type("A", (), {})()
    args.pairing_command = kw.get("sub")
    args.label = kw.get("label", "")
    args.scopes = list(kw.get("scopes", []))
    args.account = kw.get("account", "")
    args.ttl_s = kw.get("ttl_s")
    args.setup = kw.get("setup", "")
    args.confirm = kw.get("confirm", False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._run_integration(cfg, "pairing", args)
    return rc, json.loads(buf.getvalue() or "{}")


def test_setup_code_accept_through_integration_dispatcher(tmp_path,
                                                          monkeypatch):
    """OC-08: QR/join-URL setup code + channel DM pairing via the one
    existing integration dispatcher (the same path the CLI/voice use)."""
    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    rc, body = _run_integration(cfg, sub="setup-code", label="phone",
                               scopes=["messaging"], account="team",
                               confirm=True)
    assert rc == 0 and body["state"] == "OK"
    assert body["join_url"].startswith("qubit://pair")
    assert body["qr"]["format"] == "url" and body["code"] in body["qr"]["payload"]
    stored = json.loads(
        (__import__("pathlib").Path(cfg.state_dir()) /
         "pairing.json").read_text())
    assert body["code"] not in json.dumps(stored), "code shown once, hashed"

    # Unconfirmed accept is a typed gate; confirmed accept binds the DM.
    rc, bad = _run_integration(cfg, sub="accept", setup=body["join_url"])
    assert rc == 3 and bad["code"] == PA.CONFIRM_CODE
    rc, accepted = _run_integration(cfg, sub="accept", setup=body["join_url"],
                                    confirm=True)
    assert rc == 0 and accepted["setup_accepted"] is True
    assert accepted["dm_pairing"] is True
    # A consumed setup code cannot be replayed.
    rc, replay = _run_integration(cfg, sub="accept", setup=body["join_url"],
                                  confirm=True)
    assert rc == 2 and replay["code"] == "PAIRING_BAD_CODE"
