# port/chunk-6 progress

Chunk: 4 gateway/ files (highest-priority, blocker-unblocking).
Upstream ref: origin/main @ 53bec2a311979ad6f77580ba491eca261ab988e0

## Result
- ported, imports-clean: 3
  - qubit_runtime/gateway/hosted_room_task_input.py
  - qubit_runtime/gateway/hosted_room_authority_history.py
  - qubit_runtime/gateway/hosted_room_work_lineage.py  (missing dep hosted_room_work_records.py deferred)
- dep-missing: 1
  - qubit_runtime/gateway/hosted_room_attachments.py  (top-level import from a STALE
    gateway/hosted_rooms.py lacking EventAttachmentConflictError; refresh needed)
- shim-skipped: 0

## Commits
- e4ab72b7a port(gateway): chunk-6 hosted-room lineage/task-input trio
- 81b1ced86 port(gateway): chunk-6 hosted_room_attachments.py (dep-missing)

See REPORT-port-6.md and PORTED.json.
