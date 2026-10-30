"""Read-old alias for the vendored upstream module name.

The Qubit-owned module is :mod:`qubit_constants_scratch_detach`; upstream tests (and any
pre-rename caller) import the legacy name.  Aliasing the module object in ``sys.modules`` keeps
both names the *same* module, so a monkeypatch applied through either spelling reaches the live
call site.
"""
from __future__ import annotations

import sys

import qubit_constants_scratch_detach as _module

sys.modules[__name__] = _module
