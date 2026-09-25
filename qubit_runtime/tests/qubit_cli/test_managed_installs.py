from types import SimpleNamespace
from unittest.mock import patch

from qubit_cli.config import recommended_update_command
from qubit_cli.main import cmd_update
from tools.skills_hub_official import OptionalSkillSource


def test_recommended_update_command_defaults_to_qubit_update(monkeypatch):
    monkeypatch.delenv("HERMES_MANAGED", raising=False)

    # Also short-circuit the .managed marker path — CI runners may have an
    # ambient ~/.hermes/.managed if a prior test left QUBIT_HOME pointing
    # somewhere with that marker, which would make get_managed_update_command()
    # return "Update your Nix flake input ..." instead of falling through to
    # detect_install_method().
    with patch("qubit_cli.config.get_managed_update_command", return_value=None), \
         patch("qubit_cli.config.detect_install_method", return_value="git"):
        assert recommended_update_command() == "qubit update"


def test_optional_skill_source_honors_env_override(monkeypatch, tmp_path):
    optional_dir = tmp_path / "optional-skills"
    optional_dir.mkdir()
    monkeypatch.setenv("HERMES_OPTIONAL_SKILLS", str(optional_dir))

    source = OptionalSkillSource()

    assert source._optional_dir == optional_dir
