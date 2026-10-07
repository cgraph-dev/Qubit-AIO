# The `ComputerUseProvider` "regression" — root cause

## What it is NOT

It is not a missing port, and not something the sync dropped. `sync/final` **has** the
correct 199-line `backend.py` with `ComputerUseProvider` intact.

## What it IS

**The voice/orb commit `65d72246` deleted newer code.** In
`qubit_runtime/tools/computer_use/backend.py` it removed:

- the `from agent.provider_base import ProviderBase` import
- `class ComputerUseProvider(ProviderBase)` (with `create_backend`, `is_available`, `doctor`)
- an updated module docstring

`diff ded1a09a 65d72246`: **28 lines deleted, 3 added.** The file went 199 -> 174 lines.

The merge then behaved *correctly*: `sync/final` never touched `backend.py`, so git took
the only side that changed it (the voice side) with no conflict. The staleness was
already committed on the voice branch before the merge ran.

## The same clobber, a second file

`qubit_runtime/tools/computer_use/permissions.py` — **13 deleted, 1 added.** The whole
multi-backend path was removed from `computer_use_status()`:

```python
from plugins.computer_use import DEFAULT_BACKEND, configured_backend_name, get_active_provider
plat, name = sys.platform, configured_backend_name()
binary = resolve_cua_driver_cmd(driver_cmd) if name == DEFAULT_BACKEND else None
if name != DEFAULT_BACKEND:  # another driver is selected: ...
    provider = get_active_provider()
    out.update(installed=True, version=f"{provider.display_name} (computer_use.backend: {provider.name})",
               ready=provider.is_available())
```

replaced by a single line (`plat, binary = sys.platform, resolve_cua_driver_cmd(driver_cmd)`).
Main HEAD now has **0** references to `get_active_provider` in that file.

This is the "feature removal to silence a missing symbol" pattern spotted earlier in the
campaign. Now the cause is identified: the voice workstream was resumed from a snapshot
taken BEFORE the capability work landed, so its files reverted those regions.

## Why every gate missed it

The gates run against a tree. Main's tree *looks* consistent — `permissions.py` doesn't
import `get_active_provider`, so nothing dangles, and `backend.py` has no missing class. A
gate cannot see a **capability that was deleted along with all its callers**. Only a
comparison against the correct revision reveals it, which is what the completeness audit
does — and it reported `permissions.py` as a short-port for exactly this reason.

## What to do

1. **Restore both files from `sync/final`** (which holds the correct revisions).
2. **Before any future merge**, diff the voice branch against the merge-base for files
   where it DELETED more than it added — that is the clobber signature. Two files matched
   that shape here; both were real.
3. Add a gate check: for every file both branches touch, if one side's revision is an
   ancestor of the other's, flag it. Silent reverts are invisible to conflict detection.
