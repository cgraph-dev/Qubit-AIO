---
description: Compare one capability across upstream and all Qubit surfaces
agent: qubit-parity-auditor
subagent: true
---

Audit `$ARGUMENTS` against the pinned upstream source and current Qubit implementation.

Compare behavior and contracts, not filenames alone. Trace the real backend, CLI,
dashboard, voice path, persistence, errors, permissions, and tests. Do not modify
plans or acceptance criteria. Return IMPLEMENTED, SAFE_EQUIVALENT, OPERATOR_ONLY,
PARTIAL, or MISSING with file and test evidence.
