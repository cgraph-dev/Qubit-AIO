from recipes.cron import build_cron_job_spec, cron_create_command
from recipes.morning_digest import MORNING_DIGEST


def test_cron_spec_uses_the_recipe_default_schedule():
    spec = build_cron_job_spec(MORNING_DIGEST)
    assert spec["name"] == "morning-digest"
    assert spec["schedule"] == "0 8 * * *"
    assert spec["recipe"] == "morning_digest"
    assert spec["command"] == "qubit recipe run morning_digest --json"
    assert spec["deliver"] == "local"
    assert "morning_digest" in spec["prompt"]
    assert "Do not invent data" in spec["prompt"]


def test_owner_command_is_copy_pasteable_and_quoted():
    command = cron_create_command(MORNING_DIGEST)
    assert command.startswith("qubit cron create ")
    assert "0 8 * * *" in command
    assert "--name morning-digest" in command
    assert "qubit recipe run morning_digest --json" in command


def test_building_the_spec_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    build_cron_job_spec(MORNING_DIGEST)
    cron_create_command(MORNING_DIGEST)
    assert not (tmp_path / "cron").exists()
