from __future__ import annotations

import os
import subprocess
import sys


def test_platform_modules_only_import_stdlib_and_qubit_platform() -> None:
    from voicebridge import qubit_runtime

    root = qubit_runtime.ensure_path()
    code = """
import sys
before = set(sys.modules)
import qubit_platform.host.facts
import qubit_platform.host.runtime
import qubit_platform.host.products
import qubit_platform.declaration
import qubit_platform.resolver
import qubit_platform.resolver.app
import qubit_platform.resolver.availability
import qubit_platform.resolver.known_dirs
new_top_levels = {name.partition('.')[0] for name in set(sys.modules) - before}
unexpected = sorted(
    name
    for name in new_top_levels
    if name not in sys.stdlib_module_names and not name.startswith('qubit_platform')
)
assert not unexpected, unexpected
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)

    subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root),
        env=env,
        check=True,
    )
