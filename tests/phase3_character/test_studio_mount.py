from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend/qubit-app/plugins/qubit-office/game/app/AgentControlDrawer.tsx"


def test_character_studio_is_mounted_from_the_agent_control_drawer():
    source = DRAWER.read_text(encoding="utf-8")
    assert 'from "./CharacterStudio"' in source
    assert "createCharacterStudio(R, hooks)" in source
    assert '"Character Studio", "character-studio"' in source
    assert "initialProfile: { agent_id: selectedId }" in source
