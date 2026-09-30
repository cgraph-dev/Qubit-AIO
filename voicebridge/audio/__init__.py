"""Audio capture, VAD, and playback."""
from . import sndcompat  # noqa: F401  # NumPy 2.5 / sounddevice 0.5.6 compat

# Submodules are imported lazily on first attribute access. Importing them
# eagerly would pull optional audio backends (sounddevice/onnxruntime) at
# package import; exposing them lazily via PEP 562 keeps
# ``voicebridge.audio.mic`` / ``.playback`` resolvable even when a caller or a
# test has evicted the package from ``sys.modules`` and re-imported it (a fresh
# package object would otherwise lack the submodule attributes that were set by
# earlier imports).
_SUBMODULES = frozenset({"mic", "playback", "qualify", "settings", "vad"})


def __getattr__(name: str):
    if name in _SUBMODULES:
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

