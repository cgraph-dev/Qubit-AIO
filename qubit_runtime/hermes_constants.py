"""Read-old alias for the vendored upstream module name.

The Qubit-owned module is :mod:`qubit_constants`; upstream tests import the legacy spelling.
Aliasing the module object in ``sys.modules`` keeps both names the *same* module.
"""
from __future__ import annotations

import sys

import qubit_constants as _module

sys.modules[__name__] = _module
