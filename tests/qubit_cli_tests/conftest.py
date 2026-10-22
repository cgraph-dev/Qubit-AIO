"""Route bare ``gateway.*`` / ``qubit_state*`` imports to the vendored runtime."""

from voicebridge import qubit_runtime as _qubit_runtime

_qubit_runtime.ensure_path()


def pytest_configure(config):
    config.addinivalue_line("markers", "real_release_channels: touches the real release channel contract")
