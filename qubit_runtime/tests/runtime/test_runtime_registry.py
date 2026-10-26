import pytest

from agent.runtime_registry import RuntimeRegistry, build_registry
from agent.runtime_port import RuntimeCapabilities, TurnEvent, UnknownRuntimeError


class _Fake:
    name = "fake"
    capabilities = RuntimeCapabilities(False, False, False, False)

    def run_turn(self, request, cancel=None):
        yield TurnEvent("done", text="ok")


def test_register_get_and_available():
    registry = RuntimeRegistry()
    registry.register(_Fake())
    assert registry.available() == ["fake"]
    assert registry.get("fake").name == "fake"


def test_unknown_engine_is_typed_and_lists_the_registered_ones():
    registry = RuntimeRegistry()
    registry.register(_Fake())
    with pytest.raises(UnknownRuntimeError) as exc:
        registry.get("openjarvis")
    assert "fake" in str(exc.value)


def test_build_registry_registers_native():
    registry = build_registry(lambda request: object())
    assert "native" in registry.available()
