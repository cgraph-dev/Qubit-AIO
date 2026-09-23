"""Dashboard profiles: list and row actions share ONE registry (the reference).

The reference profile authority is ``qubit_cli.profiles`` — the real per-profile
home directories ``qubit agent profile`` manages. The Profiles page list, the
create, and every row action (model / description / SOUL / rename / delete /
active) now address that ONE registry, so no row shows a control that cannot
resolve. Model/provider are echoed from the profile's own config (never a
fabricated ``ollama-local``).

Hermetic: tmp XDG state + tmp HOME only; the CLI wrapper is written under the
temp HOME so no operator bin dir is touched.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _rows(cfg):
    from voicebridge import qubit_compat as QC

    code, out = QC.h_profiles(cfg, {}, {}, {})
    assert code == 200, out
    return {r["name"]: r for r in out["profiles"]}


def _create(cfg, name="p9u", **fields):
    from voicebridge import qubit_compat as QC

    body = {"name": name, "confirm": True, "no_skills": True}
    body.update(fields)
    code, out = QC.h_profile_create(cfg, {}, body, {})
    assert code == 200, out
    return out


def test_list_serves_the_reference_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rows = _rows(cfg)
    assert "default" in rows, rows  # the built-in profile always lists


def test_create_honors_fields_and_appears_in_list(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.h_profile_create(cfg, {}, {"name": "p9u"}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", out

    out = _create(cfg, "p9u", description="a probe",
                  provider="opencode-go", model="deepseek-v4-flash")
    assert out["model_set"] is True, out

    rows = _rows(cfg)
    assert "p9u" in rows, rows
    assert rows["p9u"]["model"] == "deepseek-v4-flash", rows["p9u"]
    assert rows["p9u"]["provider"] == "opencode-go", rows["p9u"]
    assert rows["p9u"]["description"] == "a probe", rows["p9u"]


def test_set_model_echoes_stored_values_not_fabricated(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, "p9u")

    code, out = QC.profile_model(
        cfg, {}, {"provider": "opencode-go", "model": "deepseek-v4-flash",
                  "confirm": True}, {"name": "p9u"})
    assert code == 200, out
    assert out["provider"] == "opencode-go", out
    assert out["model"] == "deepseek-v4-flash", out
    assert out["provider"] != "ollama-local", out

    rows = _rows(cfg)
    assert rows["p9u"]["provider"] == "opencode-go", rows["p9u"]
    assert rows["p9u"]["model"] == "deepseek-v4-flash", rows["p9u"]


def test_description_and_soul_round_trip(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, "p9u")

    code, out = QC.profile_desc(
        cfg, {}, {"description": "hello", "confirm": True}, {"name": "p9u"})
    assert code == 200 and out["description"] == "hello", out
    assert _rows(cfg)["p9u"]["description"] == "hello"

    code, out = QC.profile_soul_set(
        cfg, {}, {"content": "SOUL-BODY", "confirm": True}, {"name": "p9u"})
    assert code == 200, out
    code, out = QC.h_profile_soul(cfg, {}, {}, {"name": "p9u"})
    assert code == 200 and out["content"] == "SOUL-BODY", out


def test_rename_moves_the_profile_and_frees_the_name(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, "p9u")

    code, out = QC.profile_rename(
        cfg, {}, {"new_name": "p9u-renamed", "confirm": True}, {"name": "p9u"})
    assert code == 200 and out["name"] == "p9u-renamed", out
    rows = _rows(cfg)
    assert "p9u" not in rows and "p9u-renamed" in rows, rows


def test_delete_removes_the_profile_and_frees_the_name(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, "p9u")

    code, out = QC.profile_delete(cfg, {}, {"confirm": True}, {"name": "p9u"})
    assert code == 200 and out["removed"] is True, out
    assert "p9u" not in _rows(cfg)

    # The name is no longer occupied: a re-create succeeds.
    _create(cfg, "p9u")


def test_delete_default_is_refused(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = QC.profile_delete(cfg, {}, {"confirm": True},
                                  {"name": "default"})
    assert code == 400, out
    assert out["code"] == "PROFILE_BAD_NAME", out


def test_active_selection_round_trips(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as QC

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, "p9u")

    code, out = QC.profiles_active_set(cfg, {}, {"active": "p9u"}, {})
    assert code == 200 and out["active"] == "p9u", out
    code, out = QC.h_profiles_active(cfg, {}, {}, {})
    assert code == 200 and out["active"] == "p9u", out

    code, out = QC.profiles_active_set(cfg, {}, {"active": "nope"}, {})
    assert code == 404 and out["code"] == "PROFILE_NOT_FOUND", out
