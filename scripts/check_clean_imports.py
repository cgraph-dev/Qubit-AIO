#!/usr/bin/env python3
"""Isolated clean-install import verification (Packet 0B repair, item 3).

Proves that the listed production modules import successfully when every
third-party module NOT declared in pyproject.toml is blocked — i.e. the code
does not lean on packages that merely happen to exist in the developer venv.
Full networked pip clean-room installation remains Packet 7A scope.

Exit 0 = clean; exit 2 = an undeclared import (or other failure) was hit.

Usage:
  check_clean_imports.py                 # verify the default module list
  check_clean_imports.py --negative FILE # plant-check: FILE imports a rogue
                                         # package and MUST fail (self-test)
"""
from __future__ import annotations

import importlib.abc
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_MODULES = (
    "voicebridge.config",
    "voicebridge.redaction",
    "voicebridge.capabilities",
    "voicebridge.logging_setup",
)


def declared_modules() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    mods: set[str] = set()
    reqs = list(data.get("project", {}).get("dependencies", []))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        reqs.extend(extra)
    for r in reqs:
        name = re.split(r"[<>=!\[;\s]", r.strip(), maxsplit=1)[0]
        name = name.split("/")[-1] if "/" in name else name
        if name:
            mods.add(name.replace("-", "_").split(".")[0].lower())
    mods |= {"yaml"} if "pyyaml" in mods else set()   # dist→import alias
    return mods


class _Blocker(importlib.abc.MetaPathFinder):
    """Meta-path finder that refuses everything undeclared/stdlib/local."""

    def __init__(self, allowed: set[str]):
        self.allowed = allowed
        self.blocked: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if (top in sys.stdlib_module_names or top in self.allowed
                or top in ("voicebridge", "scripts", "_frozen_importlib")
                or fullname.startswith("_")):
            return None          # fall through to normal machinery
        self.blocked.append(fullname)
        raise ImportError(
            f"clean-install violation: '{fullname}' is not declared in "
            f"pyproject.toml and not stdlib (Packet 0B contract)")


def main(argv: list[str]) -> int:
    negative = None
    if len(argv) == 2 and argv[0] == "--negative":
        negative = Path(argv[1])
    elif argv:
        print(__doc__)
        return 2

    blocker = _Blocker(allowed=declared_modules())
    sys.meta_path.insert(0, blocker)
    targets = [str(negative)] if negative else list(DEFAULT_MODULES)
    try:
        if negative:
            spec = importlib.util.spec_from_file_location("rogue_probe", negative)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)   # expected to raise ImportError
            print(f"NEGATIVE CHECK FAILED: {negative} imported cleanly; "
                  "blocker did not fire")
            return 2
        for name in targets:
            __import__(name)
            print(f"  ok (clean): {name}")
    except ImportError as exc:
        print(f"{'EXPECTED ' if negative else ''}blocked: {exc}")
        return 0 if negative else 2
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        print(f"FAIL {exc.__class__.__name__}: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    import importlib.util
    sys.exit(main(sys.argv[1:]))
