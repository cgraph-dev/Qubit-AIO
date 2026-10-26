import pytest

from recipes.monitor_operative import MONITOR_OPERATIVE
from recipes.morning_digest import MORNING_DIGEST
from recipes.registry import RecipeRegistry, UnknownRecipeError, build_recipe_registry
from recipes.spec import RecipeSpec


def test_morning_digest_spec_declares_the_bounded_surface():
    spec = MORNING_DIGEST
    assert spec.id == "morning_digest"
    assert spec.sections == ("weather", "calendar", "unread_mail", "health")
    assert spec.default_schedule == "0 8 * * *"
    assert spec.approval_policy == "read_only_collectors"
    assert spec.output_format == "short_spoken_brief"
    assert "health" in spec.required_capabilities
    assert any("google-workspace" in tool for tool in spec.required_tools)
    assert "give me my morning digest" in spec.voice_aliases


def test_registry_lists_and_looks_up_builtins():
    registry = build_recipe_registry()
    assert registry.available() == ["monitor_operative", "morning_digest"]
    assert registry.get("morning_digest") is MORNING_DIGEST
    assert registry.get("monitor_operative") is MONITOR_OPERATIVE
    assert registry.specs() == (MONITOR_OPERATIVE, MORNING_DIGEST)


def test_unknown_recipe_lists_available_ids():
    registry = build_recipe_registry()
    with pytest.raises(UnknownRecipeError) as exc:
        registry.get("nope")
    assert "morning_digest" in str(exc.value)
    assert exc.value.available == ["monitor_operative", "morning_digest"]


def test_duplicate_registration_is_refused():
    registry = RecipeRegistry()
    registry.register(MORNING_DIGEST)
    with pytest.raises(ValueError):
        registry.register(MORNING_DIGEST)


def test_rejects_bad_id_and_empty_sections():
    with pytest.raises(ValueError):
        RecipeSpec(
            id="Bad Id", title="t", description="d", prompt="{facts}",
            sections=("weather",), required_capabilities=(),
            required_tools=(), default_schedule="0 8 * * *",
            approval_policy="read_only_collectors", output_format="text")
    with pytest.raises(ValueError):
        RecipeSpec(
            id="x", title="t", description="d", prompt="{facts}",
            sections=(), required_capabilities=(), required_tools=(),
            default_schedule="0 8 * * *",
            approval_policy="read_only_collectors", output_format="text")
