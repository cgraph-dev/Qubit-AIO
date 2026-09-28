"""Packet 8U — media generation adapters: registry + typed UNAVAILABLE gate.

Proves the OpenClaw image/music/video(/speech) adapter registry and the
request-shaping/gating contract without any live provider: catalogs report a
typed UNAVAILABLE when no credential reference is configured, generation
refuses unconfigured providers without touching the network or writing an
artifact, unknown kinds/providers are typed errors, prompt/option bounds are
enforced, costly kinds are confirmation-gated, a real transport success is
parsed into a provider asset reference (never fabricated bytes), secrets never
appear in outputs, and the emergency stop fails closed. Hermetic throughout.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from voicebridge import media_gen as MG


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    """Fake user config under throwaway XDG dirs (no real config touched).

    Provider credential env vars are cleared so an ambient/leaked value (e.g.
    the vendored Hermes runtime's dotenv load) can never make an unconfigured
    adapter look ready; tests that need one set it explicitly after this.
    """
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    for caps in MG.PROVIDER_CAPS.values():
        var = str(caps.get("credential_env") or "").strip()
        if var:
            monkeypatch.delenv(var, raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _no_network(monkeypatch):
    """Any attempt to reach a provider must fail the test, not the network."""
    def _boom(req, timeout=None):
        raise AssertionError("media generation must not touch the network here")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


class _Resp:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gen(**kw):
    cfg = kw.pop("cfg")
    kw.setdefault("kind", "image")
    kw.setdefault("provider", "openai")
    kw.setdefault("prompt", "a red fox")
    kw.setdefault("confirmed", True)
    return MG.generate(cfg, **kw)


# --------------------------------------------------------------- registries
def test_registry_shape_and_overlap():
    assert tuple(MG.PROVIDERS) == tuple(MG.PROVIDER_CAPS)
    assert set(MG.KINDS) == {"image", "music", "video", "speech"}
    assert "local" in MG.PROVIDERS, "local/overlap row must be visible"
    for pid in MG.PROVIDERS:
        caps = MG.PROVIDER_CAPS[pid]
        assert caps["label"]
        assert caps["kinds"], pid
        assert caps["credential_kind"]
        for kind, cap in caps["kinds"].items():
            assert kind in MG.KINDS, (pid, kind)
            assert cap.get("transport"), (pid, kind)
    ready = [(p, k) for p in MG.PROVIDERS
             for k in MG.PROVIDER_CAPS[p]["kinds"]
             if MG.PROVIDER_CAPS[p]["kinds"][k].get("available")]
    assert ready == [("openai", "image")], "only the real adapter is ready"
    speech = MG.PROVIDER_CAPS["local"]["kinds"]["speech"]
    assert speech["overlap"] is True
    assert "voicebridge/tts" in MG.KIND_META["speech"]["overlap"]
    assert MG.PROVIDER_KINDS["openai"] == ("image", "speech")


def test_catalog_unavailable_without_creds(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cat = MG.catalog(cfg)
    assert cat["state"] == "OK" and cat["schema"] == MG.CATALOG_SCHEMA
    assert cat["rows"]
    for row in cat["rows"]:
        assert {"kind", "provider", "state", "reason"} <= set(row)
        assert row["state"] in ("AVAILABLE", "UNAVAILABLE")
    openai_image = next(r for r in cat["rows"]
                        if r["provider"] == "openai" and r["kind"] == "image")
    assert openai_image["state"] == "UNAVAILABLE"
    assert openai_image["code"] == MG.UNAVAILABLE_CODE
    assert openai_image["reason"]
    assert "openai:image" in cat["unavailable"]
    assert cat["available"] == []


def test_status_derives_from_references(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    st = MG.status(cfg)
    assert st["state"] == "OK" and st["schema"] == MG.STATUS_SCHEMA
    by = {p["provider"]: p for p in st["providers"]}
    assert by["openai"]["configured"] is False
    assert by["openai"]["credential_ref_kind"] == "env"
    assert by["openai"]["kinds"]["image"]["state"] == "UNAVAILABLE"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-media-key-value-8u")
    st2 = MG.status(cfg)
    by2 = {p["provider"]: p for p in st2["providers"]}
    assert by2["openai"]["configured"] is True
    assert by2["openai"]["credential_ref_kind"] == "env"
    assert by2["openai"]["kinds"]["image"]["state"] == "AVAILABLE"
    assert "openai:image" in st2["configured"]
    # Configured but no adapter: still honestly unavailable.
    assert by2["fal"]["kinds"]["image"]["state"] == "UNAVAILABLE"
    assert by2["fal"]["kinds"]["image"]["reason"]
    # The value never leaks into the reference-only projection.
    assert "sk-test-media-key-value-8u" not in json.dumps(st2)


def test_explicit_key_ref_wins_over_env(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, extra=(
        "media_gen:\n  providers:\n    openai:\n"
        "      key_ref: env:CUSTOM_OPENAI_REF_8U\n"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ignored-default-8u")
    monkeypatch.delenv("CUSTOM_OPENAI_REF_8U", raising=False)
    assert MG.reference_for(cfg, "openai") == "env:CUSTOM_OPENAI_REF_8U"
    by = {p["provider"]: p for p in MG.status(cfg)["providers"]}
    assert by["openai"]["configured"] is False, "explicit ref must win"
    monkeypatch.setenv("CUSTOM_OPENAI_REF_8U", "sk-custom-ref-value-8u")
    by2 = {p["provider"]: p for p in MG.status(cfg)["providers"]}
    assert by2["openai"]["configured"] is True
    assert "CUSTOM_OPENAI_REF_8U" in MG.reference_for(cfg, "openai")


# --------------------------------------------------------------- generate gates
def test_generate_unconfigured_provider_typed_and_no_artifact(tmp_path,
                                                              monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _no_network(monkeypatch)
    state_dir = Path(cfg.state_dir())
    before = sorted(str(p) for p in state_dir.rglob("*"))
    with pytest.raises(MG.MediaGenError) as excinfo:
        _gen(cfg=cfg, prompt="a red fox")
    assert excinfo.value.code == MG.UNAVAILABLE_CODE
    after = sorted(str(p) for p in state_dir.rglob("*"))
    assert before == after, "an unconfigured generate must write nothing"


def test_unknown_kind_and_provider_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    with pytest.raises(MG.MediaGenError) as e1:
        _gen(cfg=cfg, kind="sculpture")
    assert e1.value.code == MG.KIND_UNKNOWN_CODE
    with pytest.raises(MG.MediaGenError) as e2:
        _gen(cfg=cfg, provider="ghost")
    assert e2.value.code == MG.PROVIDER_UNKNOWN_CODE
    # Known provider without that kind is an honest typed gap, not "unknown".
    with pytest.raises(MG.MediaGenError) as e3:
        _gen(cfg=cfg, provider="runway", kind="image")
    assert e3.value.code == MG.UNAVAILABLE_CODE


def test_prompt_and_option_bounds(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    for bad in [dict(prompt=""), dict(prompt="   "),
                dict(prompt="x" * (MG.MAX_PROMPT_CHARS + 1)),
                dict(options=[]), dict(options={"bogus": 1}),
                dict(options={"n": 0}), dict(options={"n": 5}),
                dict(options={"n": True}), dict(options={"n": "2"}),
                dict(options={"size": "1024"}),
                dict(options={"size": "99999x99999"}),
                dict(options={"model": "x" * (MG.MAX_OPTION_TEXT + 1)})]:
        with pytest.raises(MG.MediaGenError) as excinfo:
            _gen(cfg=cfg, **bad)
        assert excinfo.value.code == MG.BAD_INPUT_CODE, bad
    # Per-kind duration bounds for a costly kind.
    with pytest.raises(MG.MediaGenError) as e:
        _gen(cfg=cfg, kind="music", provider="fal", prompt="lofi",
             options={"duration_s": 0})
    assert e.value.code == MG.BAD_INPUT_CODE
    with pytest.raises(MG.MediaGenError) as e2:
        _gen(cfg=cfg, kind="music", provider="fal", prompt="lofi",
             options={"duration_s": 9999})
    assert e2.value.code == MG.BAD_INPUT_CODE
    # A well-formed option set passes validation (then hits the cred gate).
    with pytest.raises(MG.MediaGenError) as e3:
        _gen(cfg=cfg, options={"size": "512x512", "n": 1})
    assert e3.value.code == MG.UNAVAILABLE_CODE


def test_confirmation_required_for_costly_kinds(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    for kind, provider in (("music", "fal"), ("video", "runway")):
        with pytest.raises(MG.MediaGenError) as excinfo:
            MG.generate(cfg, kind=kind, provider=provider, prompt="x",
                        confirmed=False)
        assert excinfo.value.code == MG.CONFIRM_CODE, kind
    # A non-costly kind with no creds is UNAVAILABLE, not a confirmation ask.
    with pytest.raises(MG.MediaGenError) as e:
        MG.generate(cfg, kind="image", provider="openai", prompt="x",
                    confirmed=False)
    assert e.value.code == MG.UNAVAILABLE_CODE


def test_adapterless_provider_with_creds_never_fakes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("FAL_KEY", "fal-secret-value-8u")
    _no_network(monkeypatch)
    with pytest.raises(MG.MediaGenError) as excinfo:
        _gen(cfg=cfg, provider="fal", kind="image")
    assert excinfo.value.code == MG.UNAVAILABLE_CODE
    assert "fal-secret-value-8u" not in excinfo.value.detail


def test_build_request_is_pure_shaping():
    req = MG.build_request("openai", "image", "a cat",
                           {"size": "512x512", "n": 2})
    assert req["method"] == "POST"
    assert req["url"] == MG.ENDPOINTS["openai-images"]
    assert req["body"] == {"model": "gpt-image-1", "prompt": "a cat", "n": 2,
                           "size": "512x512"}
    with pytest.raises(MG.MediaGenError) as excinfo:
        MG.build_request("fal", "image", "x")
    assert excinfo.value.code == MG.UNAVAILABLE_CODE


def test_success_only_via_real_transport_and_redacted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret-value-8u")
    calls = []

    def _fake(req, timeout=None):
        calls.append(req)
        auth = req.get_header("Authorization", "")
        assert auth.startswith("Bearer "), "key in header only"
        assert "sk-live-secret-value-8u" not in req.data.decode()
        body = json.loads(req.data.decode())
        assert body["prompt"] == "a blue circle"
        assert body["size"] == "512x512"
        return _Resp({"data": [{
            "id": "img-1",
            "url": "https://cdn.example/asset.png?token=leakvalue8u"}]})

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    out = MG.generate(cfg, kind="image", provider="openai",
                      prompt="a blue circle", options={"size": "512x512"},
                      confirmation_id="conf-1", confirmed=True, source="voice")
    assert out["state"] == "OK" and out["schema"] == MG.SCHEMA
    assert out["provider"] == "openai" and out["kind"] == "image"
    assert out["asset"]["provider_asset_id"] == "img-1"
    assert out["asset"]["url"].startswith("https://cdn.example/")
    assert out["prompt_sha256"]
    assert len(calls) == 1, "exactly one transport attempt"
    blob = json.dumps(out)
    assert "sk-live-secret-value-8u" not in blob
    assert "leakvalue8u" not in blob, "URL secret query is redacted"
    assert not list(Path(cfg.state_dir()).rglob("*.png")), "no artifact written"


def test_success_hashes_b64_never_stores_bytes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-b64-secret-8u")

    def _fake(req, timeout=None):
        return _Resp({"data": [{"id": "img-b64", "b64_json": "aGVsbG8="}]})

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    out = _gen(cfg=cfg, prompt="a green square")
    assert out["state"] == "OK"
    assert out["asset"]["encoding"] == "base64"
    assert out["asset"]["b64_sha256"]
    blob = json.dumps(out)
    assert "aGVsbG8=" not in blob, "raw provider bytes are never stored"
    assert "sk-b64-secret-8u" not in blob


def test_redaction_of_prompt_and_outputs(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-redactcheck1234567890")

    def _fake(req, timeout=None):
        return _Resp({"data": [{"id": "i", "url": "https://cdn.example/a.png"}]})

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    out = _gen(cfg=cfg, prompt="draw token=promptsecret8u now")
    blob = json.dumps(out)
    assert "sk-redactcheck1234567890" not in blob
    assert "promptsecret8u" not in blob
    cat = json.dumps(MG.catalog(cfg))
    st = json.dumps(MG.status(cfg))
    assert "sk-redactcheck1234567890" not in cat
    assert "sk-redactcheck1234567890" not in st


def test_estop_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.power import power_control_store

    monkeypatch.setenv("OPENAI_API_KEY", "sk-estop-secret-8u")
    _no_network(monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        assert MG.status(cfg)["estop"] is True
        with pytest.raises(MG.MediaGenError) as excinfo:
            _gen(cfg=cfg, prompt="anything")
        assert excinfo.value.code == MG.ESTOP_CODE
        assert MG.refuse_estop("generate")["code"] == MG.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_status_and_catalog_never_contain_credential_values(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    secret = tmp_path / "openai-key"
    secret.write_text("file-secret-value-8u\n")
    cfg2 = _cfg(tmp_path, monkeypatch, extra=(
        "media_gen:\n  providers:\n    openai:\n"
        f"      key_ref: file:{secret}\n"))
    st = MG.status(cfg2)
    by = {p["provider"]: p for p in st["providers"]}
    assert by["openai"]["configured"] is True
    assert by["openai"]["credential_ref_kind"] == "file"
    blob = json.dumps(st) + json.dumps(MG.catalog(cfg2))
    assert "file-secret-value-8u" not in blob
    assert str(secret) not in blob
