# REPORT-port-6 — chunk-6: gateway hosted-room lineage/task-input family

**Upstream ref:** `origin/main` @ `53bec2a311979ad6f77580ba491eca261ab988e0` (2026-10-16, read via `git show`, never the pin).
**Worktree:** `/tmp/port-6`, branch `port/chunk-6`.
**Interpreter:** `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python`.
**Mapping:** upstream root == `qubit_runtime/`.
**Result:** 3 ported (imports-clean), 1 ported-but-`dep-missing`. 0 shim-skipped.

## Verdict per file

| upstream path | our path | up-lines | status | imports-clean |
|---|---|---|---|---|
| `gateway/hosted_room_task_input.py` | `qubit_runtime/gateway/hosted_room_task_input.py` | 24 | **ported** | ✅ |
| `gateway/hosted_room_authority_history.py` | `qubit_runtime/gateway/hosted_room_authority_history.py` | 114 | **ported** | ✅ |
| `gateway/hosted_room_work_lineage.py` | `qubit_runtime/gateway/hosted_room_work_lineage.py` | 40 | **ported** | ✅ (one deferred missing dep) |
| `gateway/hosted_room_attachments.py` | `qubit_runtime/gateway/hosted_room_attachments.py` | 1363 | **dep-missing** | ❌ (top-level dep absent) |

Commits: `e4ab72b7a` (trio), `81b1ced86` (attachments).

## What unblocks (this was the point of the chunk)

Evidence of the `import` proof for each file:

```
cd /tmp/port-6/qubit_runtime && PYTHONPATH=/tmp/port-6/qubit_runtime .venv/bin/python -c "import gateway.hosted_room_task_input"        # exit 0
                                                                                     -c "import gateway.hosted_room_authority_history"  # exit 0
                                                                                     -c "import gateway.hosted_room_work_lineage"       # exit 0
                                                                                     -c "import gateway.hosted_room_attachments"        # ImportError (see below)
```

- **`hosted_room_task_input.py`** — pure stdlib (`typing.Any`). Nothing imports it back yet; safe to depend on now.
- **`hosted_room_authority_history.py`** — its only dep is `gateway.hosted_rooms_common.identifier`, which **EXISTS** and whose signature (`value, *, label, error, max_chars, pattern, invalid`) matches upstream exactly. Fully live.
- **`hosted_room_work_lineage.py`** — top-level import is `gateway.hosted_room_authority_history` (lands in this same chunk, so it resolves). Its `gateway.hosted_room_work_records` imports are **deferred inside functions** (lines 6 and 28, confirmed indented), so the module imports clean today. `hosted_room_work_records.py` is absent and **in the 299-list (line 54)** — name it for the next wave.

## The one blocker, named

`gateway/hosted_room_attachments.py` line 12 is a **top-level** import:

```python
from gateway.hosted_rooms import EventAttachmentConflictError
```

Our ported `qubit_runtime/gateway/hosted_rooms.py` (**1189 lines**, landed by the sync commit `25232ac99` "upstream subtree") is a **stale revision**. Upstream HEAD's `hosted_rooms.py` is **1212 lines** and adds, at upstream lines 196–201:

```python
class EventCursorConflictError(HostedRoomError): ...
class EventAttachmentConflictError(EventCursorConflictError): ...
```

Both are absent from our copy, so the import raises `ImportError: cannot import name 'EventAttachmentConflictError' from 'gateway.hosted_rooms'`.

**This is a revision skew, not a missing file** — `hosted_rooms.py` is *not* in the 299-list. The full delta is only **41 changed lines**:

- the two new exception classes (196–201),
- `admit: Callable[[sqlite3.Connection], Any] | None = None` param on the create path,
- `expected_latest_seq: int | None = None` param on the event path + the `EventCursorConflictError` raise it guards,
- two **deferred** calls: `gateway.hosted_room_work_records.capture_transition_locked` (line 816) and **this file's** `retain_message_attachments` (line 991) — the latter is a *mutual* dependency between `hosted_rooms.py` and `hosted_room_attachments.py`.

### Proof the blocker is the stale dependency, not this port

- `py_compile qubit_runtime/gateway/hosted_room_attachments.py` → **passes**.
- Scratch probe (a **copy** of the tree, not my ported file): inject the two missing classes into the copied `hosted_rooms.py`, then `import gateway.hosted_room_attachments` →
  `OK True True True` (`retain_message_attachments`, `HostedRoomAttachmentStore`, `fold_catalog_text` all resolve). So the module is sound the moment its dependency's revision is reconciled.

### Refresh needed to unblock it (recommendation, not done here)

Refresh `qubit_runtime/gateway/hosted_rooms.py` to upstream HEAD (`53bec2a3`). Its two **new** external deps are both imported **inside functions**, so the refreshed file still imports:

- `gateway.hosted_room_work_records.capture_transition_locked` → absent, **299-list line 54**; deferred.
- `gateway.hosted_room_attachments.retain_message_attachments` → this port; deferred.

Adding the two classes is additive and the added params are keyword-with-defaults, so a refresh is backward-compatible for existing `hosted_rooms` callers. **I did not apply it** — `hosted_rooms.py` is outside this chunk's 4 files and owned by a sync commit; refreshing it would silently pull the `hosted_room_work_records` chunk into this lane's diff. That is the next wave's ordering item.

## Brand reconciliation

- Zero brand tokens in the three proven files — ported **verbatim**.
- `hosted_room_attachments.py` had exactly one: `from hermes_state_wal import apply_wal_with_fallback` (line 417, deferred) → renamed **`hermes_state_wal` → `qubit_state_wal`** per §3a. `qubit_runtime/qubit_state_wal.py` **EXISTS** (not in the 299-list).
- Grep for the `HERMES_*`/`hermes_state_wal` spellings across `qubit_runtime/` returned **no collision** — no other file uses either spelling, so no third inconsistency was created.
- Wire/protocol identifiers in these files (`hosted_room_events`, `authority.claimed`, `previous_gateway_id`, `authority-control`) are data, left verbatim.

## Gateway invariants (qubit_runtime/gateway/AGENTS.md)

Read before porting. **None of the four files engage** the streaming-delivery contract (four invariants — they never touch `draft_stream_is_message`, `finish()`/`send()` egress, or `delivered_final_matches`), the **two-message-guards** rule (no inbound-message/approval dispatch), the `/login` path, or the profile-scope identity seams (`resolve_identity`, `_intake_adapter_for`/`_delivery_adapter_for`, `_profile_runtime_scope`). They are **data/policy modules**: SQLite-backed hosted-room event storage, authority-span replay validation, task-input bounding, and message-attachment retention. The profile-scope rule *does* surface nearby — `hosted_rooms.py`'s `_attachment_root` resolves `<QUBIT_HOME>/profiles/<name>/` via `qubit_constants.get_qubit_home()` — but that lives in the dependency, not in these four files, and an unbound read there is the stale-revision path already noted.
