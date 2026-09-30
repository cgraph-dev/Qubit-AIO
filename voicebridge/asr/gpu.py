"""CUDA preload helper for the sherpa-onnx inference runtime.

The GPU-enabled sherpa-onnx wheel bundles ``libonnxruntime_providers_cuda.so``
but NOT the CUDA/cuDNN runtime it links against; those come from the
``nvidia-*-cu12`` pip packages. The dynamic loader resolves a shared object's
``NEEDED`` sonames against already-loaded libraries, so preloading the CUDA
libraries with ``RTLD_GLOBAL`` (low-level first) lets the CUDA provider load.

Everything here is best-effort and idempotent: if the CUDA libraries or the GPU
provider are absent, :func:`ensure_cuda_libraries` returns ``False`` and the ASR
layer falls back to the CPU provider. No second speech backend is introduced.
"""
from __future__ import annotations

import ctypes
import threading
from pathlib import Path
from typing import Dict, List

_lock = threading.Lock()
_loaded = False
_preloaded: List[str] = []
_error = ""

# Low-level first (nvJitLink before cublas/cudnn; cudart before the rest).
_ORDER = ("cuda_runtime", "nvjitlink", "cublas", "cufft", "curand",
          "cuda_nvrtc", "cudnn")


def _site_packages() -> Path | None:
    try:
        import sherpa_onnx  # noqa: PLC0415
        return Path(sherpa_onnx.__file__).resolve().parent.parent
    except Exception:
        return None


def cuda_provider_present() -> bool:
    """True when the installed sherpa-onnx build ships the CUDA provider."""
    sp = _site_packages()
    if sp is None:
        return False
    return (sp / "sherpa_onnx" / "lib"
            / "libonnxruntime_providers_cuda.so").exists()


def ensure_cuda_libraries() -> bool:
    """Preload the pip CUDA/cuDNN libraries once; True when anything loaded."""
    global _loaded, _error
    with _lock:
        if _loaded:
            return True
        sp = _site_packages()
        if sp is None:
            _error = "sherpa_onnx not importable"
            return False
        nv = sp / "nvidia"
        if not nv.is_dir():
            _error = f"no vendored CUDA libraries under {nv}"
            return False
        for name in _ORDER:
            libdir = nv / name / "lib"
            if not libdir.is_dir():
                continue
            for so in sorted(libdir.glob("*.so*")):
                try:
                    ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
                    _preloaded.append(so.name)
                except OSError as exc:  # pragma: no cover - env dependent
                    _error = f"{so.name}: {exc}"
        _loaded = bool(_preloaded)
        return _loaded


def status() -> Dict[str, object]:
    """Non-secret CUDA readiness for the health route."""
    return {
        "cuda_provider_present": cuda_provider_present(),
        "cuda_libraries_loaded": _loaded,
        "preloaded_count": len(_preloaded),
        "error": _error[:200],
    }
