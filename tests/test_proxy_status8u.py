"""OC-D08 — deny-by-default secrets egress proxy.

Hermetic: no network, no real environment secrets. Proves the broker contract
(allowlist, references-not-values, stream substitution, fail-closed typed
errors) and that ``proxy_status`` still refuses to run a forwarding server
while exposing the replacement egress surface.
"""
from __future__ import annotations

import pytest

from voicebridge import proxy_status as PS
from voicebridge import proxy_secrets as PXS


def _cfg(tmp_path, monkeypatch, body: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + body)
    return C.Config.load(p)


def _registry(monkeypatch, value="s3cr3t-value"):
    monkeypatch.setenv("QUBIT_TEST_SECRET", value)
    reg = PXS.SecretRegistry()
    reg.register("api", "env:QUBIT_TEST_SECRET")
    return reg


# ------------------------------------------------------------------ status
def test_status_reports_no_server_and_policy():
    st = PS.status()
    assert st["state"] == "NOT_RUNNING"
    assert st["forwards"] == 0
    assert "minimum-necessary egress" in st["policy"]


def test_refuse_start_is_typed():
    r = PS.refuse_start()
    assert r["state"] == "REFUSED"
    assert r["code"] == PS.NO_SERVER_CODE


def test_egress_status_never_contains_values(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch,
               "proxy:\n  allowed_hosts:\n    - api.example.com\n")
    reg = _registry(monkeypatch, "top-secret-xyz")
    st = PS.egress_status(cfg, registry=reg)
    assert st["state"] == "OK"
    assert st["server"]["code"] == PS.NO_SERVER_CODE
    assert st["policy"]["allowed_hosts"] == ["api.example.com"]
    assert st["registry"]["count"] == 1
    assert st["registry"]["secrets"] == [{"name": "api", "kind": "env"}]
    assert "top-secret-xyz" not in repr(st)


def test_check_egress_deny_by_default(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    denied = PS.check_egress(cfg, "evil.example.com")
    assert denied["allowed"] is False
    assert denied["code"] == PXS.EGRESS_DENIED


def test_replace_forwarder_substitutes_only_for_allowlist(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch,
               "proxy:\n  allowed_hosts:\n    - api.example.com\n")
    reg = _registry(monkeypatch, "abc123")
    out = PS.substitute_egress(cfg, "Authorization: {{secret:api}}",
                               "api.example.com", registry=reg)
    assert out == "Authorization: abc123"
    with pytest.raises(PXS.EgressError) as ei:
        PS.substitute_egress(cfg, "x", "evil.example.com", registry=reg)
    assert ei.value.code == PXS.EGRESS_DENIED


# ---------------------------------------------------------------- registry
def test_registry_rejects_literal_values():
    reg = PXS.SecretRegistry()
    with pytest.raises(PXS.EgressError) as ei:
        reg.register("bad", "sk-literal-value")
    assert ei.value.code == PXS.EGRESS_BAD_REFERENCE
    assert reg.rejections == 1
    assert reg.names() == []


def test_registry_rejects_bad_name():
    reg = PXS.SecretRegistry()
    with pytest.raises(PXS.EgressError) as ei:
        reg.register("bad name!", "env:X")
    assert ei.value.code == PXS.EGRESS_BAD_REFERENCE


def test_registry_missing_secret_is_typed_and_value_free():
    reg = PXS.SecretRegistry(environ={})
    reg.register("absent", "env:NOT_SET_ANYWHERE")
    with pytest.raises(PXS.EgressError) as ei:
        reg.resolve("absent")
    assert ei.value.code == PXS.EGRESS_SECRET_MISSING
    assert "NOT_SET_ANYWHERE" not in ei.value.detail


def test_registry_file_reference_roundtrip(tmp_path, monkeypatch):
    secret_file = tmp_path / "token"
    secret_file.write_text("file-secret-9876\n")
    reg = PXS.SecretRegistry()
    reg.register("tok", f"file:{secret_file}")
    assert reg.resolve("tok") == "file-secret-9876"
    assert reg.status()["secrets"] == [{"name": "tok", "kind": "file"}]
    assert "file-secret-9876" not in repr(reg.status())


def test_registry_unregister_and_full():
    reg = PXS.SecretRegistry(environ={}, max_secrets=1)
    reg.register("a", "env:A")
    with pytest.raises(PXS.EgressError):
        reg.register("b", "env:B")
    assert "a" in reg
    assert reg.unregister("a") is True
    assert reg.unregister("a") is False


# ---------------------------------------------------------------- policy
def test_policy_deny_by_default_and_normalization():
    pol = PXS.EgressPolicy()
    assert pol.decide("")[0] is False
    assert pol.decide("example.com")[0] is False

    pol = PXS.EgressPolicy(allowed_hosts=["API.Example.COM:443"])
    assert pol.allowed_hosts == ["api.example.com"]
    assert pol.decide("api.example.com") == (True, "ALLOWLISTED")
    assert pol.decide("https://user:pw@api.example.com/x")[0] is True
    assert pol.decide("other.example.com")[0] is False


def test_policy_allow_all_is_explicit():
    pol = PXS.EgressPolicy(allow_all=True)
    assert pol.decide("anything.example")[0] is True
    assert pol.check("anything.example")["code"] == "OK"


# ----------------------------------------------------------- substitution
def test_substitute_replaces_complete_placeholder(monkeypatch):
    reg = _registry(monkeypatch, "value-123")
    pol = PXS.EgressPolicy(allowed_hosts=["api.example.com"])
    assert PXS.substitute("k={{secret:api}}", pol, reg,
                          "api.example.com") == "k=value-123"


def test_substitute_denied_never_resolves(monkeypatch):
    reg = _registry(monkeypatch, "value-123")
    pol = PXS.EgressPolicy(allowed_hosts=["api.example.com"])
    with pytest.raises(PXS.EgressError) as ei:
        PXS.substitute("{{secret:api}}", pol, reg, "evil.example.com")
    assert ei.value.code == PXS.EGRESS_DENIED
    assert "value-123" not in ei.value.detail


def test_substitute_unknown_placeholder(monkeypatch):
    reg = PXS.SecretRegistry(environ={})
    pol = PXS.EgressPolicy(allowed_hosts=["api.example.com"])
    with pytest.raises(PXS.EgressError) as ei:
        PXS.substitute("{{secret:nope}}", pol, reg, "api.example.com")
    assert ei.value.code == PXS.EGRESS_SECRET_MISSING


def test_substitute_incomplete_placeholder_fails_closed(monkeypatch):
    reg = _registry(monkeypatch)
    pol = PXS.EgressPolicy(allowed_hosts=["api.example.com"])
    with pytest.raises(PXS.EgressError) as ei:
        PXS.substitute("{{secret:api", pol, reg, "api.example.com")
    assert ei.value.code == PXS.EGRESS_INCOMPLETE_PLACEHOLDER


def test_stream_substitution_across_chunk_boundaries(monkeypatch):
    reg = _registry(monkeypatch, "streamed-value")
    pol = PXS.EgressPolicy(allowed_hosts=["api.example.com"])
    chunks = ["Authorization: {{sec", "ret:api}}\n", "done"]
    out = "".join(PXS.substitute_stream(chunks, pol, reg, "api.example.com"))
    assert out == "Authorization: streamed-value\ndone"


def test_stream_incomplete_at_end_fails_closed(monkeypatch):
    reg = _registry(monkeypatch)
    pol = PXS.EgressPolicy(allowed_hosts=["api.example.com"])
    gen = PXS.substitute_stream(["a {{secret:ap"], pol, reg,
                                "api.example.com")
    with pytest.raises(PXS.EgressError) as ei:
        "".join(gen)
    assert ei.value.code == PXS.EGRESS_INCOMPLETE_PLACEHOLDER


def test_stream_denied_before_yielding_any_chunk(monkeypatch):
    reg = _registry(monkeypatch)
    pol = PXS.EgressPolicy()
    with pytest.raises(PXS.EgressError) as ei:
        next(PXS.substitute_stream(["x"], pol, reg, "evil.example.com"))
    assert ei.value.code == PXS.EGRESS_DENIED


def test_placeholders_lists_names_only():
    assert PXS.placeholders("a {{secret:one}} b {{secret:two}}") == ["one",
                                                                    "two"]
