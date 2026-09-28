# Operations — Packet 8T: setup, health, and lifecycle dashboard

Backend: `voicebridge/lifecycle.py` (WRAP of local contracts — no upstream
code, no new dependency). CLI: `voicebridge setup …`. Dashboard: `/setup`
page + `GET /api/setup/health|checklist|diagnostics`, `POST /api/setup/run`.

## Read-only first (safe on any machine)

- `voicebridge setup check` — staged checklist with resume pointer.
- `voicebridge setup check-offline` — offline degraded mode (no network).
- `voicebridge setup health` — full redacted inventory (references only).
- `voicebridge setup diagnose` — redacted diagnostic bundle (no secrets).
- `voicebridge setup matrix|wizard|repair|audit` — capability matrix, audio
  wizard data, ordered repair plan, bounded audit timeline.

## Mutations (explicit approval only)

- `voicebridge setup run` — dry-run preview (default, changes nothing).
- `voicebridge setup run --apply --confirm [--steps a,b]` — staged apply.
  Without `--confirm` every mutation is DENIED and nothing is created.
  Unknown step ids fail closed (the whole request is FAILED, never narrowed).
- `voicebridge setup export [--out F]` — preview by default (writes
  nothing). `--apply --confirm` writes atomically: validated destination
  inside an existing directory, symlink destinations refused, unique
  exclusive temp file, fsynced content + directory, 0600 mode, validated
  `.bak-<ts>` backup of any existing file, own-temp-only cleanup, 256 KiB
  output cap, post-write verification. Without both flags the result is
  PENDING (preview) or DENIED (unconfirmed) and the filesystem is untouched.
- `voicebridge setup backup plan|migrate|migrate-rollback|rollback
  [--apply --confirm] [--backup-dir D]` — migration/backup center.
- `voicebridge setup upgrade [--apply --confirm]` — always a truthful
  PENDING preview or BLOCKED operator handoff: this backend never executes
  installers, so it never reports SUCCESS for an upgrade it did not perform.
  The confirmed response carries the exact operator command
  (`scripts/install.sh`, idempotent, preserves tuned units with `.bak`).
  `voicebridge setup rollback` previews/validates via the product rollback
  contract with partial-failure visibility.

## Dashboard parity

The `/setup` page calls the same `lifecycle` backend through authenticated
`GET/POST /api/setup/*` (header token only; query-string tokens are always
401). `POST /api/setup/run` previews by default and rejects unknown steps
with typed FAILED; `POST /api/setup/export` shares the CLI export contract
(preview unless `{apply:true, confirm:true}`, destination confined to the
state directory). Applying from the browser asks twice (`window.confirm` x2).

## Typed states

`PENDING RUNNING READY SUCCESS MISSING UNAVAILABLE DEGRADED BLOCKED FAILED
DENIED CANCELLED SKIPPED`. Detection is never permission; a UI toggle never
executes anything. Offline/missing providers are `UNAVAILABLE`, never fake
readiness. Unknown operations fail closed (`FAILED`).

## Rollback

- Setup runs record an `InstallJournal` receipt with a rollback pointer;
  `setup rollback --apply --confirm` reverses applied steps.
- Exports keep a `.bak-<ts>` next to the output; restores preview first.
- Code rollback: `git revert` the 8T commit (no migrations shipped).

## Limits (explicit)

- Packet 8S has no independent VERIFIED record at 8T build time; the 8T
  backend reuses 8S/8K-P paths read-only and changes no identity behavior.
- No live service/timer/browser/account is ever enabled by this packet.
- `pip check` equivalent: `uv pip check --python .venv/bin/python` (the venv
  ships without pip; no new dependency was added).
