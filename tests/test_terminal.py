from voicebridge.terminal import QUBIT_LOGO, _qubit_banner


def test_qubit_banner_contains_supplied_logo_and_identity():
    rendered = _qubit_banner()
    assert QUBIT_LOGO.strip() in rendered
    assert "QUBIT VOICE BRIDGE" in rendered
    assert "LOCAL-FIRST AI TERMINAL" in rendered
    assert "\033[" not in rendered


def test_qubit_banner_color_is_presentation_only():
    rendered = _qubit_banner(color=True)
    assert "QUBIT VOICE BRIDGE" in rendered
    assert "\033[38;5;39m" in rendered
    assert rendered.endswith("\033[0m")
