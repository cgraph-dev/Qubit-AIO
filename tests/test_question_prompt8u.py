"""Packet 8U/D21 — structured multi-question prompts + pending store.

Hermetic: isolated XDG/state under tmp_path, no network. Proves contract
creation, choice/text validation, persistence permissions, expiry and
replay rejection, unknown/estop refusals, and voice/CLI rendering.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

import voicebridge.question_prompt as QP


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _questions():
    return [
        {"id": "deploy", "text": "Deploy now?", "kind": "choice",
         "choices": ["yes", "no"]},
        {"text": "Which branch?", "kind": "text"},
    ]


def test_status(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = QP.status(cfg)
    assert st["state"] == "OK"
    assert st["kinds"] == ["choice", "text"]
    assert st["pending"] == 0
    assert st["store"].endswith("question-prompts.json")


def test_ask_and_answer_roundtrip_and_permissions(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = QP.ask(cfg, _questions(), session="voice", confirmed=True)
    assert out["state"] == "OK"
    assert out["prompt_id"].startswith("qp")
    assert out["answers"] is None
    assert [q["id"] for q in out["questions"]] == ["deploy", "q2"]
    assert out["questions"][1]["kind"] == "text"

    path = QP._store_path(cfg)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with open(path) as fh:
        stored = json.load(fh)
    assert stored["prompts"][out["prompt_id"]]["session"] == "voice"
    assert stored["prompts"][out["prompt_id"]]["answered"] is False

    assert QP.status(cfg)["pending"] == 1
    done = QP.answer(cfg, out["prompt_id"],
                     {"deploy": "yes", "q2": "release/8u"})
    assert done["state"] == "OK"
    assert done["answers"] == {"deploy": "yes", "q2": "release/8u"}
    assert QP.status(cfg)["pending"] == 0


def test_answer_accepts_list_form(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = QP.ask(cfg, _questions(), confirmed=True)
    done = QP.answer(cfg, out["prompt_id"], [
        {"id": "deploy", "value": "no"},
        {"id": "q2", "value": "main"},
    ])
    assert done["answers"] == {"deploy": "no", "q2": "main"}


def test_replay_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = QP.ask(cfg, _questions(), confirmed=True)
    QP.answer(cfg, out["prompt_id"], {"deploy": "yes", "q2": "main"})
    with pytest.raises(QP.QuestionError) as exc:
        QP.answer(cfg, out["prompt_id"], {"deploy": "yes", "q2": "main"})
    assert exc.value.code == QP.UNKNOWN_CODE


def test_expiry_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    base = [1000.0]
    monkeypatch.setattr(QP, "_now", lambda: base[0])
    out = QP.ask(cfg, _questions(), confirmed=True)
    base[0] = out["expires_at"] + 1.0
    with pytest.raises(QP.QuestionError) as exc:
        QP.answer(cfg, out["prompt_id"], {"deploy": "yes", "q2": "main"})
    assert exc.value.code == QP.EXPIRED_CODE


def test_unknown_prompt_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(QP.QuestionError) as exc:
        QP.answer(cfg, "qpdeadbeef", {"x": "y"})
    assert exc.value.code == QP.UNKNOWN_CODE


def test_validation_bad_input(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = QP.ask(cfg, _questions(), confirmed=True)
    pid = out["prompt_id"]
    for bad in (
        {"deploy": "maybe", "q2": "main"},
        {"deploy": "yes"},
        {"deploy": "yes", "q2": ""},
        {"deploy": "yes", "q2": "main", "extra": "x"},
        {"deploy": "yes", "q2": 5},
    ):
        with pytest.raises(QP.QuestionError) as exc:
            QP.answer(cfg, pid, bad)
        assert exc.value.code == QP.BAD_INPUT_CODE, bad
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, [{"text": ""}], confirmed=True)
    assert exc.value.code == QP.BAD_INPUT_CODE
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, [{"text": "pick", "kind": "choice",
                      "choices": []}], confirmed=True)
    assert exc.value.code == QP.BAD_INPUT_CODE
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, [{"id": "Bad Id", "text": "hi"}], confirmed=True)
    assert exc.value.code == QP.BAD_INPUT_CODE
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, [{"id": "a", "text": "one"},
                     {"id": "a", "text": "two"}], confirmed=True)
    assert exc.value.code == QP.BAD_INPUT_CODE


def test_too_many_questions_and_choices(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    many = [{"text": f"question {i}"} for i in range(QP.MAX_QUESTIONS + 1)]
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, many, confirmed=True)
    assert exc.value.code == QP.TOO_MANY_CODE
    wide = [{"text": "pick", "kind": "choice",
             "choices": ["a", "b", "c", "d"]}]
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, wide, choice_limit=3, confirmed=True)
    assert exc.value.code == QP.TOO_MANY_CODE


def test_confirm_required(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(QP.QuestionError) as exc:
        QP.ask(cfg, _questions())
    assert exc.value.code == QP.CONFIRM_CODE


def test_estop_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(QP.QuestionError) as exc:
            QP.ask(cfg, _questions(), confirmed=True)
        assert exc.value.code == QP.ESTOP_CODE
        with pytest.raises(QP.QuestionError) as exc:
            QP.answer(cfg, "qpwhatever", {"a": "b"})
        assert exc.value.code == QP.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_pending_and_render(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = QP.ask(cfg, _questions(), confirmed=True)
    items = QP.pending(cfg)["pending"]
    assert len(items) == 1 and items[0]["prompt_id"] == out["prompt_id"]
    text = QP.render(out)
    assert "[yes]" in text and "[no]" in text
    assert "free text" in text
    assert QP.render({"questions": out["questions"][0]}).count("\n") == 0
