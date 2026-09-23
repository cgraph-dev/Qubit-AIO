"""Dashboard profiles: list and row actions share ONE registry (the principal store).

The Profiles page listed voice/brain manifests from ``config_profiles`` while
every row action (model/description/delete/rename) addressed the ``brain``
principal registry — so listed rows' controls could not resolve. The list now
serves the principal registry the actions write, create honors the submitted
fields, model/provider are echoed in clear (never a fabricated ``ollama-local``
or a masked placeholder), and delete removes the record it claims to.

Hermetic: tmp XDG state only.
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


def _names(cfg):
    from voicebridge import qubit_compat as QC

    code, out = QC.h_profiles(cfg, {}, {}, {})
    assert code == 200, out
    return {r["name"]: r for r in out["profiles"]}


def test_create_honors_fields_and_appears_in_list(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.h_profile_create(
        cfg, {}, {"name": "p9u"}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", out

    code, out = QC.h_profile_create(
        cfg, {}, {"name": "p9u", "confirm": True,
                  "description": "a probe", "provider": "opencode-go",
                  "model": "deepseek-v4-flash"}, {})
    assert code == 200, out
    assert out["model"] == "deepseek-v4-flash", out
    assert out["provider"] == "opencode-go", out
    assert out["description"] == "a probe", out

    rows = _names(cfg)
    assert "p9u" in rows, rows
    assert rows["p9u"]["model"] == "deepseek-v4-flash", rows["p9u"]
    assert rows["p9u"]["provider"] == "opencode-go", rows["p9u"]
    assert rows["p9u"]["description"] == "a probe", rows["p9u"]


def test_set_model_echoes_stored_values_not_fabricated(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    QC.h_profile_create(cfg, {}, {"name": "p9u", "confirm": True}, {})

    code, out = QC.profile_model(
        cfg, {}, {"provider": "opencode-go", "model": "deepseek-v4-flash",
                  "confirm": True}, {"name": "p9u"})
    assert code == 200, out
    assert out["provider"] == "opencode-go", out
    assert out["model"] == "deepseek-v4-flash", out
    assert out["provider"] != "ollama-local", out

    rows = _names(cfg)
    assert rows["p9u"]["provider"] == "opencode-go", rows["p9u"]
    assert rows["p9u"]["model"] == "deepseek-v4-flash", rows["p9u"]


def test_delete_removes_the_principal_and_frees_the_name(tmp_path,
                                                         monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    QC.h_profile_create(cfg, {}, {"name": "p9u", "confirm": True}, {})

    code, out = QC.profile_delete(cfg, {}, {"confirm": True}, {"name": "p9u"})
    assert code == 200 and out["removed"] is True, out
    assert "p9u" not in _names(cfg)

    # The name is no longer occupied: a re-create succeeds (previously the
    # principal survived and this 400'd BRAIN_EXISTS).
    code, out = QC.h_profile_create(cfg, {}, {"name": "p9u", "confirm": True},
                                    {})
    assert code == 200, out


def test_rename_is_a_typed_immutable_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    QC.h_profile_create(cfg, {}, {"name": "p9u", "confirm": True}, {})
    code, out = QC.profile_rename(cfg, {}, {}, {"name": "p9u"})
    assert code == 501, out
    assert out["code"] == "PERSONA_IMMUTABLE_ID", out
    assert out["qubit_counterpart"], out
