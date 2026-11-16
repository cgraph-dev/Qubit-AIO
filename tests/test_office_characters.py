"""Habblaud character customization is durable and reaches the snapshot."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import office_stream as os
from voicebridge.office import characters as CH


class Cfg:
    def __init__(self, root: Path):
        self._state = root / "state"

    def state_dir(self) -> str:
        return str(self._state)


def test_save_load_and_reset_are_scoped_to_the_office_state(tmp_path: Path):
    cfg = Cfg(tmp_path)
    row = CH.save(cfg, "42", {"name": " Ada Lovelace ", "seed": 77,
                              "parts": {"skin": "#FFEEDD", "hairStyle": "bob"}})
    assert row == {"name": "Ada Lovelace", "seed": 77,
                   "parts": {"skin": "#ffeedd", "hairStyle": "bob"}}
    path = CH.path(cfg)
    assert path == tmp_path / "state" / "office" / "characters.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["schema"] == CH.SCHEMA
    assert CH.load(cfg)["42"] == row
    assert CH.reset(cfg, "42") is True
    assert CH.reset(cfg, "42") is False
    assert CH.load(cfg) == {}


@pytest.mark.parametrize("body", [
    {"name": "", "seed": 1},
    {"name": "x", "seed": -1},
    {"name": "x", "seed": 1.5},
    {"name": "x", "seed": 1, "parts": {"skin": "red"}},
    {"name": "x", "seed": 1, "parts": {"hairStyle": "nope"}},
    {"name": "x", "seed": 1, "parts": {"unknown": "x"}},
    {"name": "x" * 25, "seed": 1},
])
def test_invalid_customization_is_rejected_without_writing(tmp_path: Path, body):
    cfg = Cfg(tmp_path)
    with pytest.raises(CH.CharacterError):
        CH.save(cfg, "1", body)
    assert not CH.path(cfg).exists()


def test_snapshot_forwards_saved_name_seed_and_parts():
    fixture = (Path(__file__).resolve().parent.parent / "frontend" / "qubit-web"
               / "src" / "lib" / "office-game" / "fixtures" / "tower-recording.json")
    t = json.loads(fixture.read_text(encoding="utf-8"))
    ch = t["characters"][0]
    ch.update({"display_name": "Ada", "seed": 99,
               "parts": {"top": "#112233"}, "custom": True})
    snap = os.to_snapshot(t, t["now"], t["now"])
    a = snap["agents"][0]
    assert a["name"] == "Ada"
    assert a["seed"] == 99
    assert a["parts"] == {"top": "#112233"}
    assert a["custom"] is True
