"""Make the vendored runtime's top-level packages importable, in isolation.

``providers``, ``agent``, ``qubit_cli`` and ``qubit_constants`` are top-level
modules under the fork root (``qubit_runtime/``). ``ensure_path()`` is the one
sanctioned way to put that root on ``sys.path``; without it the ported provider
tests cannot import at collection time.

``QUBIT_HOME`` is sandboxed to a throwaway dir so a provider profile /
``config.yaml`` never resolves against the operator's real home. Two hooks are
needed: a module-scope set (before ``ensure_path()`` pins import-time paths) and
a per-test autouse fixture, because the repo root conftest's autouse
``_isolate_qubit_runtime`` runs ``monkeypatch.delenv("QUBIT_HOME")`` before every
test — so a module-scope value alone is cleared by the time a test body runs.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import pytest

_SANDBOX_HOME = tempfile.mkdtemp(prefix="qubit-test-providers-home-")
atexit.register(shutil.rmtree, _SANDBOX_HOME, True)
os.environ["QUBIT_HOME"] = _SANDBOX_HOME

from voicebridge import qubit_runtime  # noqa: E402

qubit_runtime.ensure_path()


@pytest.fixture(autouse=True)
def _sandbox_qubit_home(monkeypatch):
    """Re-pin QUBIT_HOME after the repo conftest's autouse delenv clears it."""
    monkeypatch.setenv("QUBIT_HOME", _SANDBOX_HOME)


@pytest.fixture(autouse=True)
def _reset_named_custom_memo(monkeypatch):
    """Clear the process-global named-custom memo between tests.

    ``providers._NAMED_CUSTOM_MEMO`` / ``..._SIG`` are module globals keyed by
    home; a test that monkeypatches ``has_named_custom_provider`` (e.g.
    ``test_bare_named_custom_provider_gets_custom_profile``) leaves an entry that
    a later memo-isolation test reads, making its "no config -> False" first
    assertion fail. Upstream's ``tests/conftest.py`` gave each file a fresh
    session home; Qubit's root conftest does not, so reset the memo here.
    """
    import providers

    monkeypatch.setattr(providers, "_NAMED_CUSTOM_MEMO", {})
    monkeypatch.setattr(providers, "_NAMED_CUSTOM_MEMO_SIG", {})
