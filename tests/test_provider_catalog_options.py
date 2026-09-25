"""Provider/model catalogue parity — ``/api/model/options`` delegation.

Owner decision #5: Qubit is a rebranded fork of the real Hermes foundation, so
the dashboard model picker must delegate to the vendored
``qubit_cli.inventory`` builder (the SAME builder the reference dashboard
serves), not a hand-rolled 3-profile stub.

These tests exercise the real handler against a temp ``QUBIT_HOME`` with a
minimal runtime config, asserting the wire contract ``ModelPickerDialog``
consumes and the Qubit policy that no credential value/reference is served.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A minimal Qubit Config whose runtime home holds ``config.yaml``."""
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    voice = tmp_path / "voice.yaml"
    voice.write_text("logging:\n  jsonl: false\n")
    c = C.Config.load(voice)
    # The vendored picker reads config.yaml from QUBIT_HOME; give it a real one
    # so the catalogue is built against a known provider rather than ambient
    # operator state.
    home = c.state_dir() / "runtime"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        textwrap.dedent(
            """\
            model:
              provider: opencode-go
              default: deepseek-v4-flash
              base_url: https://opencode.ai/zen/go/v1
            """
        )
    )
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return c


def _q(**kw):
    return {k: str(v) for k, v in kw.items()}


def test_options_delegates_to_vendored_and_returns_many_providers(cfg):
    """The real builder is used: >3 provider rows, curated models, wire shape."""
    from voicebridge import qubit_compat as QC

    status, body = QC.h_model_options(cfg, _q(include_unconfigured=True), {}, {})
    assert status == 200 and body.get("ok") is True

    providers = body.get("providers")
    assert isinstance(providers, list)
    # The stub returned 3; the vendored universe is far larger. Assert the
    # contract (more than a handful), not a snapshot count.
    assert len(providers) > 10, [p.get("slug") for p in providers]

    slugs = [p.get("slug") for p in providers]
    assert "opencode-go" in slugs
    # Every row carries the picker contract fields.
    for p in providers:
        assert isinstance(p.get("name"), str) and p["name"]
        assert isinstance(p.get("slug"), str) and p["slug"]
        assert isinstance(p.get("models"), list)
        assert p.get("total_models") == len(p["models"])
        assert isinstance(p.get("authenticated"), bool)

    # A current row exists and matches the configured selection.
    current = [p for p in providers if p.get("is_current")]
    assert current, slugs
    assert body.get("model") == "deepseek-v4-flash"
    assert body.get("provider") == "opencode-go"


def test_options_default_is_configured_subset(cfg):
    """Without ``include_unconfigured`` the payload is the configured subset."""
    from voicebridge import qubit_compat as QC

    _, body = QC.h_model_options(cfg, {}, {}, {})
    providers = body.get("providers") or []
    assert providers, "configured subset must not be empty"
    assert len(providers) < 20, [p.get("slug") for p in providers]


def test_options_explicit_only_filters_to_configured(cfg):
    """``explicit_only`` keeps only explicitly configured providers."""
    from voicebridge import qubit_compat as QC

    _, all_body = QC.h_model_options(cfg, _q(include_unconfigured=True), {}, {})
    _, body = QC.h_model_options(cfg, _q(explicit_only=True), {}, {})
    assert len(body.get("providers") or []) <= len(all_body.get("providers") or [])


def test_options_never_serves_credential_values_or_refs(cfg):
    """Qubit policy: no secret value and no credential reference leaves the box."""
    from voicebridge import qubit_compat as QC

    _, body = QC.h_model_options(cfg, _q(include_unconfigured=True), {}, {})
    blob = json.dumps(body)
    # No credential-reference keys anywhere.
    for key in ("key_env", "key_ref", "api_key_env", "secret_ref"):
        # ``authenticated`` / ``auth_type`` booleans/labels are allowed; the
        # reference carriers are not.
        assert f'"{key}"' not in blob, key
    # No raw secret-shaped values.
    for marker in ("access_token", "refresh_token", "client_secret",
                   "private_key", "sk-"):
        assert marker not in blob, marker


def test_model_info_reports_configured_model_shape(cfg):
    """``/api/model/info`` reports the configured model with the wire shape."""
    from voicebridge import qubit_compat as QC

    status, body = QC.h_model_info(cfg, {}, {}, {})
    assert status == 200 and body.get("ok") is True
    # In an isolated home the resolver may land on the shipped local default;
    # the contract is the wire shape + positive context, never a provider
    # snapshot. (The live operator home resolves opencode-go — see evidence.)
    assert isinstance(body.get("model"), str) and body["model"]
    assert isinstance(body.get("provider"), str) and body["provider"]
    assert int(body.get("effective_context_length") or 0) > 0
    caps = body.get("capabilities") or {}
    assert "context_window" in caps


def test_recommended_default_uses_vendored_curation(cfg):
    """A known provider slug yields the vendored recommended-default shape."""
    from voicebridge import qubit_compat as QC

    status, body = QC.h_model_recommended_default(
        cfg, _q(provider="opencode-go"), {}, {})
    assert status == 200 and body.get("provider") == "opencode-go"
    # No credentials in an isolated home, so the model may be empty; it must
    # never be a silently-picked paid default. Assert the shape, not a value.
    assert isinstance(body.get("model"), str)
    assert body.get("free_tier") is None

    status, body = QC.h_model_recommended_default(
        cfg, _q(provider="no-such-provider"), {}, {})
    assert status == 200 and body.get("provider") == "no-such-provider"
    assert body.get("model") == ""


def test_auxiliary_reads_real_config_slots(cfg):
    """``/api/model/auxiliary`` returns the real main assignment + task slots."""
    from voicebridge import qubit_compat as QC

    status, body = QC.h_model_auxiliary(cfg, {}, {}, {})
    assert status == 200 and body.get("ok") is True
    assert isinstance(body.get("tasks"), list)
    main = body.get("main") or {}
    assert main.get("provider") == "opencode-go"
    assert main.get("model") == "deepseek-v4-flash"


def test_resolved_model_shape_when_profile_persisted(tmp_path, monkeypatch):
    """With a persisted high-context profile the resolver returns it exec-ably."""
    from voicebridge import config as C
    from voicebridge.providers import resolver as R
    from voicebridge.providers.resolver import ModelProfile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    voice = tmp_path / "voice.yaml"
    voice.write_text("logging:\n  jsonl: false\n")
    c = C.Config.load(voice)

    prof = ModelProfile(
        id="catalog-hi", kind="api", provider="opencode-go",
        model="deepseek-v4-flash", base_url="https://opencode.ai/zen/go/v1",
        key_ref="env:CATALOG_API_KEY", locality="external",
        egress_consent="allow", context_tokens=200000,
        supports_tools=True, supports_streaming=True,
    )
    out = R.upsert_model_profile(c, prof, confirm=True)
    assert out.get("state") == "OK", out

    eff = R.resolve_model(c, requested="catalog-hi")
    # Profile resolution reports the real target + declared context window.
    # Executability is gated separately by the frontier policy (egress
    # allowlist); this test owns catalogue/profile resolution, not policy.
    assert eff.get("profile_id") == "catalog-hi"
    assert eff.get("provider") == "opencode-go"
    assert eff.get("model") == "deepseek-v4-flash"
    assert int(eff.get("context_tokens") or 0) >= 65536
