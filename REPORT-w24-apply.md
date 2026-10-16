# sync-w24 — SAFE-subset application report

**Branch applied into:** `agent/catchup-3` (worktree `/tmp/catchup-ag3`), base `1c740851e`
**Source branch:** `sync-w24` @ `8efc0dd62043e3f1085b9900871e07b812389e51` (2026-10-05)
**Take list:** `docs/sync-w24/w24_take.txt` (86 paths)

## Result

| Bucket | Count |
|---|---:|
| **APPLIED** | **2** |
| **DEFERRED** | **84** |
| **FAILED** | **0** |

Only **2 of 86** TAKE paths are safe to land. The other 84 are all
**1634-commits-stale** snapshots whose wholesale application would delete
HEAD content added after the branch's 2026-10-05 base — real feature code,
not churn. This is a well-argued "almost all deferred": TAKE meant the content
was worth having, not that it was a safe drop-in.

## Safety rules used

A path is APPLIED only when:

1. **(a) absent from HEAD** — a pure add, cannot conflict; or
2. **(b) HEAD's content is a strict line-superset of the applied result** and no
   branch-only line is a brand-token regression (Hermes where HEAD has Qubit).

Everything that would REPLACE newer HEAD content, or that differs on a brand
token, is DEFERRED with the reason.

## Method (reproducible)

```sh
# per candidate path:
git show sync-w24:$f > b ; git show HEAD:$f > h
comm -23 <(sort b) <(sort h)   # branch-only lines  (b_minus_h)
comm -13 <(sort b) <(sort h)   # HEAD-only lines    (h_minus_b)
```

`h_minus_b == 0` => HEAD is a line-superset => rule (b) candidate. The computed
`b_minus_h`/`h_minus_b` figures reproduce `docs/sync-w24/REPORT-w24.md` exactly
(e.g. `backend-ready.ts` 54/0, `AGENTS.md` 25/9, `bootstrap.rs` 104/23), which
validates the harness against the prior analysis.

Brand check: every branch-only line was grepped for the `Hermes`/`hermes` token;
a token that HEAD's counterpart does not carry (or that HEAD brands as Qubit) is a
regression. Deliberate compat/provenance names (`HERMES_*` env vars,
`hermes_cli`/`hermes_platform` subsystem ids, upstream repo paths) are permitted —
see `docs/HERMES_RESIDUE_INVENTORY.md`.

---

## APPLIED

| path | commit | branch lines added | HEAD lines removed | justification |
|---|---|---:|---:|---|
| `docs/SYNC_REMAINING.md` | `e2bff4ecb` | 208 | 0 | Path absent from HEAD; pure add, cannot conflict. Branch-only Hermes occurrences are upstream provenance (hermes_cli/hermes_platform subsystem names, ~/.cache/qubit-pins/hermes-agent), classified DELIBERATE by docs/HERMES_RESIDUE_INVENTORY.md - not product branding. |
| `qubit_runtime/apps/desktop/electron/backend-ready.ts` | `fe1db5df5` | 54 | 0 | Branch-only content is the coherent #122206 source-completion grace feature; applied as a strict superset of HEAD (verified 0 HEAD lines absent, incl. HEAD's Qubit-branded reject line byte-identical). One brand regression (branch renamed the port-announce error to 'Hermes') was reverted to HEAD's 'Qubit' wording - the only brand-token conflict in the file. All HERMES_* uppercase tokens are deliberate env-var compat, identical in HEAD. |

### Detail

**`docs/SYNC_REMAINING.md`** — rule a - absent from HEAD (clean add)
- Path absent from HEAD; pure add, cannot conflict. Branch-only Hermes occurrences are upstream provenance (hermes_cli/hermes_platform subsystem names, ~/.cache/qubit-pins/hermes-agent), classified DELIBERATE by docs/HERMES_RESIDUE_INVENTORY.md - not product branding.

**`qubit_runtime/apps/desktop/electron/backend-ready.ts`** — rule b - branch content applied to HEAD is additive (HEAD is a line superset after brand restore)
- Branch-only content is the coherent #122206 source-completion grace feature; applied as a strict superset of HEAD (verified 0 HEAD lines absent, incl. HEAD's Qubit-branded reject line byte-identical). One brand regression (branch renamed the port-announce error to 'Hermes') was reverted to HEAD's 'Qubit' wording - the only brand-token conflict in the file. All HERMES_* uppercase tokens are deliberate env-var compat, identical in HEAD.
- Note: The branch refactors HEAD's inline 'const timer = setTimeout(...)' reject block into rearmTimer(); that 4-line block is re-expressed (logic + Qubit error string preserved verbatim inside rearmTimer), not lost.

---

## DEFERRED

All 84 share one root cause: the branch predates HEAD by 1634 commits, so its
version of each file lacks HEAD's later additions. Applying it wholesale would
revert those additions. Any path with a brand-token conflict is called out.

| path | b_only | h_only | HEAD lines lost | Hermes-only branch lines | reason |
|---|---:|---:|---:|---:|---|
| `qubit_runtime/apps/desktop/electron/bootstrap-runner.ts` | 43 | 59 | 59 | 8 | Replaces newer HEAD content: applying wholesale drops 59 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=43, h_only=59). 1634 commits stale. Also carries 8 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx` | 127 | 55 | 55 | 3 | Replaces newer HEAD content: applying wholesale drops 55 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=127, h_only=55). 1634 commits stale. Also carries 3 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx` | 141 | 52 | 52 | 1 | Replaces newer HEAD content: applying wholesale drops 52 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=141, h_only=52). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/src/pages/plugins/index.tsx` | 68 | 49 | 49 | 3 | Replaces newer HEAD content: applying wholesale drops 49 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=68, h_only=49). 1634 commits stale. Also carries 3 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/developer-guide/relay-shared-metrics.md` | 268 | 46 | 46 | 33 | Replaces newer HEAD content: applying wholesale drops 46 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=268, h_only=46). 1634 commits stale. Also carries 33 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/getting-started/updating.md` | 48 | 41 | 41 | 7 | Replaces newer HEAD content: applying wholesale drops 41 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=48, h_only=41). 1634 commits stale. Also carries 7 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx` | 237 | 36 | 36 | 1 | Replaces newer HEAD content: applying wholesale drops 36 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=237, h_only=36). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/bootstrap-installer/src-tauri/src/lib.rs` | 45 | 35 | 35 | 1 | Replaces newer HEAD content: applying wholesale drops 35 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=45, h_only=35). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/store/updates.ts` | 119 | 34 | 34 | 1 | Replaces newer HEAD content: applying wholesale drops 34 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=119, h_only=34). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/user-guide/configuration.md` | 46 | 30 | 30 | 4 | Replaces newer HEAD content: applying wholesale drops 30 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=46, h_only=30). 1634 commits stale. Also carries 4 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/scripts/_activation.sh` | 27 | 28 | 28 | 5 | Replaces newer HEAD content: applying wholesale drops 28 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=27, h_only=28). 1634 commits stale. Also carries 5 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts` | 82 | 25 | 25 | 1 | Replaces newer HEAD content: applying wholesale drops 25 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=82, h_only=25). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/store/onboarding.ts` | 118 | 24 | 24 | 1 | Replaces newer HEAD content: applying wholesale drops 24 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=118, h_only=24). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/store/projects.ts` | 92 | 24 | 24 | 2 | Replaces newer HEAD content: applying wholesale drops 24 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=92, h_only=24). 1634 commits stale. Also carries 2 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/bootstrap-installer/src-tauri/src/bootstrap.rs` | 104 | 23 | 23 | 1 | Replaces newer HEAD content: applying wholesale drops 23 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=104, h_only=23). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/user-guide/features/plugin-catalog.md` | 34 | 23 | 23 | 4 | Replaces newer HEAD content: applying wholesale drops 23 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=34, h_only=23). 1634 commits stale. Also carries 4 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/chat/right-rail/preview-pane.tsx` | 113 | 22 | 22 | 3 | Replaces newer HEAD content: applying wholesale drops 22 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=113, h_only=22). 1634 commits stale. Also carries 3 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/lib/chat-messages/hydration.ts` | 88 | 20 | 20 | 1 | Replaces newer HEAD content: applying wholesale drops 20 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=88, h_only=20). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.test.ts` | 118 | 17 | 17 | 3 | Replaces newer HEAD content: applying wholesale drops 17 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=118, h_only=17). 1634 commits stale. Also carries 3 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron/updater-process.ts` | 30 | 16 | 16 | 7 | Replaces newer HEAD content: applying wholesale drops 16 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=30, h_only=16). 1634 commits stale. Also carries 7 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron/ssh-connection.ts` | 85 | 15 | 15 | 1 | Replaces newer HEAD content: applying wholesale drops 15 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=85, h_only=15). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts` | 82 | 15 | 15 | 2 | Replaces newer HEAD content: applying wholesale drops 15 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=82, h_only=15). 1634 commits stale. Also carries 2 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/lib/voice-client-direct.ts` | 52 | 15 | 15 | 5 | Replaces newer HEAD content: applying wholesale drops 15 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=52, h_only=15). 1634 commits stale. Also carries 5 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/developer-guide/plugins/index.md` | 56 | 15 | 15 | 3 | Replaces newer HEAD content: applying wholesale drops 15 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=56, h_only=15). 1634 commits stale. Also carries 3 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/themes/backend-sync.ts` | 47 | 12 | 12 | 2 | Replaces newer HEAD content: applying wholesale drops 12 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=47, h_only=12). 1634 commits stale. Also carries 2 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/themes/context.tsx` | 40 | 11 | 11 | 1 | Replaces newer HEAD content: applying wholesale drops 11 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=40, h_only=11). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/developer-guide/desktop-plugin-sdk.md` | 141 | 11 | 11 | 1 | Replaces newer HEAD content: applying wholesale drops 11 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=141, h_only=11). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/AGENTS.md` | 25 | 9 | 9 | 1 | Replaces newer HEAD content: applying wholesale drops 9 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=25, h_only=9). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/src/pages/skills/index.tsx` | 68 | 9 | 9 | 1 | Replaces newer HEAD content: applying wholesale drops 9 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=68, h_only=9). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron/remote-ws-headers.test.ts` | 57 | 8 | 8 | 4 | Replaces newer HEAD content: applying wholesale drops 8 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=57, h_only=8). 1634 commits stale. Also carries 4 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/status.ts` | 30 | 8 | 8 | 1 | Replaces newer HEAD content: applying wholesale drops 8 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=30, h_only=8). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/plugins/hermes-bots/i18n.ts` | 62 | 8 | 8 | 13 | Replaces newer HEAD content: applying wholesale drops 8 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=62, h_only=8). 1634 commits stale. Also carries 13 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/user-guide/features/kanban.md` | 24 | 8 | 8 | 1 | Replaces newer HEAD content: applying wholesale drops 8 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=24, h_only=8). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/plugin-catalog/README.md` | 53 | 7 | 7 | 8 | Replaces newer HEAD content: applying wholesale drops 7 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=53, h_only=7). 1634 commits stale. Also carries 8 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/chat/sidebar/profile-rail-fleet.test.tsx` | 99 | 6 | 6 | 8 | Replaces newer HEAD content: applying wholesale drops 6 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=99, h_only=6). 1634 commits stale. Also carries 8 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub.tsx` | 46 | 6 | 6 | 1 | Replaces newer HEAD content: applying wholesale drops 6 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=46, h_only=6). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/sdk/index.ts` | 89 | 6 | 6 | 2 | Replaces newer HEAD content: applying wholesale drops 6 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=89, h_only=6). 1634 commits stale. Also carries 2 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron-builder.config.cjs` | 26 | 4 | 4 | 1 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=26, h_only=4). 1634 commits stale. Also carries 1 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/right-sidebar/index.test.tsx` | 37 | 4 | 4 | 2 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=37, h_only=4). 1634 commits stale. Also carries 2 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/nix/checks.nix` | 25 | 4 | 4 | 3 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=25, h_only=4). 1634 commits stale. Also carries 3 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron/remote-lifecycle.test.ts` | 127 | 3 | 3 | 2 | Replaces newer HEAD content: applying wholesale drops 3 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=127, h_only=3). 1634 commits stale. Also carries 2 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/website/docs/guides/cron-script-only.md` | 23 | 3 | 3 | 4 | Replaces newer HEAD content: applying wholesale drops 3 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=23, h_only=3). 1634 commits stale. Also carries 4 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/electron/venv-holder-select.test.ts` | 23 | 1 | 1 | 11 | Replaces newer HEAD content: applying wholesale drops 1 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=23, h_only=1). 1634 commits stale. Also carries 11 Hermes-branded branch-only line(s) absent from HEAD (brand-token conflict). |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/utils.ts` | 301 | 67 | 67 | 0 | Replaces newer HEAD content: applying wholesale drops 67 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=301, h_only=67). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/context-menu/app-context-menu.tsx` | 26 | 60 | 60 | 0 | Replaces newer HEAD content: applying wholesale drops 60 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=26, h_only=60). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts` | 131 | 50 | 50 | 0 | Replaces newer HEAD content: applying wholesale drops 50 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=131, h_only=50). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts` | 64 | 39 | 39 | 0 | Replaces newer HEAD content: applying wholesale drops 39 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=64, h_only=39). 1634 commits stale. |
| `qubit_runtime/apps/desktop/electron/bootstrap-runner.test.ts` | 28 | 33 | 33 | 0 | Replaces newer HEAD content: applying wholesale drops 33 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=28, h_only=33). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/chat/session-tile.tsx` | 158 | 32 | 32 | 0 | Replaces newer HEAD content: applying wholesale drops 32 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=158, h_only=32). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/store/review.ts` | 126 | 29 | 29 | 0 | Replaces newer HEAD content: applying wholesale drops 29 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=126, h_only=29). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/store/session.ts` | 131 | 27 | 27 | 0 | Replaces newer HEAD content: applying wholesale drops 27 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=131, h_only=27). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-background-sync.ts` | 189 | 24 | 24 | 0 | Replaces newer HEAD content: applying wholesale drops 24 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=189, h_only=24). 1634 commits stale. |
| `qubit_runtime/apps/desktop/electron/update-handoff-marker.test.ts` | 31 | 18 | 18 | 0 | Replaces newer HEAD content: applying wholesale drops 18 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=31, h_only=18). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts` | 83 | 15 | 15 | 0 | Replaces newer HEAD content: applying wholesale drops 15 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=83, h_only=15). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/components/assistant-ui/directive-text.tsx` | 29 | 15 | 15 | 0 | Replaces newer HEAD content: applying wholesale drops 15 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=29, h_only=15). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/chat/transcript-backfill.ts` | 248 | 14 | 14 | 0 | Replaces newer HEAD content: applying wholesale drops 14 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=248, h_only=14). 1634 commits stale. |
| `qubit_runtime/website/docs/developer-guide/context-compression-and-caching.md` | 27 | 14 | 14 | 0 | Replaces newer HEAD content: applying wholesale drops 14 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=27, h_only=14). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.test.ts` | 181 | 13 | 13 | 0 | Replaces newer HEAD content: applying wholesale drops 13 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=181, h_only=13). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/lib/stale-transcript-guard.ts` | 101 | 13 | 13 | 0 | Replaces newer HEAD content: applying wholesale drops 13 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=101, h_only=13). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/artifacts/artifact-utils.ts` | 38 | 12 | 12 | 0 | Replaces newer HEAD content: applying wholesale drops 12 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=38, h_only=12). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/store/projects.test.ts` | 100 | 11 | 11 | 0 | Replaces newer HEAD content: applying wholesale drops 11 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=100, h_only=11). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/right-sidebar/files/ipc.ts` | 73 | 10 | 10 | 0 | Replaces newer HEAD content: applying wholesale drops 10 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=73, h_only=10). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/use-message-reactions.ts` | 38 | 10 | 10 | 0 | Replaces newer HEAD content: applying wholesale drops 10 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=38, h_only=10). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/lib/desktop-fs.ts` | 35 | 8 | 8 | 0 | Replaces newer HEAD content: applying wholesale drops 8 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=35, h_only=8). 1634 commits stale. |
| `qubit_runtime/apps/desktop/electron/minimize-to-tray.ts` | 41 | 7 | 7 | 0 | Replaces newer HEAD content: applying wholesale drops 7 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=41, h_only=7). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/chat/right-rail/real-profile-consent-dialog.tsx` | 42 | 7 | 7 | 0 | Replaces newer HEAD content: applying wholesale drops 7 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=42, h_only=7). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-background-sync.test.ts` | 113 | 7 | 7 | 0 | Replaces newer HEAD content: applying wholesale drops 7 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=113, h_only=7). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/components/chat/shiki-highlighter.tsx` | 37 | 7 | 7 | 0 | Replaces newer HEAD content: applying wholesale drops 7 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=37, h_only=7). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/plugins/hermes-bots/relay.test.ts` | 103 | 6 | 6 | 0 | Replaces newer HEAD content: applying wholesale drops 6 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=103, h_only=6). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/settings/terminal-backend-panel.test.tsx` | 21 | 5 | 5 | 0 | Replaces newer HEAD content: applying wholesale drops 5 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=21, h_only=5). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/chat/composer/hooks/use-composer-drop.ts` | 23 | 4 | 4 | 0 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=23, h_only=4). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/chat/transcript-backfill.test.ts` | 141 | 4 | 4 | 0 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=141, h_only=4). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/contrib/hooks/completed-reply-refresh.test.tsx` | 66 | 4 | 4 | 0 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=66, h_only=4). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/profiles/index.test.tsx` | 24 | 4 | 4 | 0 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=24, h_only=4). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/settings/helpers.ts` | 27 | 4 | 4 | 0 | Replaces newer HEAD content: applying wholesale drops 4 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=27, h_only=4). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/store/reactions.test.ts` | 62 | 3 | 3 | 0 | Replaces newer HEAD content: applying wholesale drops 3 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=62, h_only=3). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/store/review.test.ts` | 146 | 3 | 3 | 0 | Replaces newer HEAD content: applying wholesale drops 3 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=146, h_only=3). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/store/session.test.ts` | 140 | 3 | 3 | 0 | Replaces newer HEAD content: applying wholesale drops 3 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=140, h_only=3). 1634 commits stale. |
| `qubit_runtime/apps/desktop/electron/entry.ts` | 34 | 2 | 2 | 0 | Replaces newer HEAD content: applying wholesale drops 2 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=34, h_only=2). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts` | 24 | 2 | 2 | 0 | Replaces newer HEAD content: applying wholesale drops 2 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=24, h_only=2). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/components/language-switcher.test.tsx` | 35 | 2 | 2 | 0 | Replaces newer HEAD content: applying wholesale drops 2 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=35, h_only=2). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/plugins/hermes-bots/model-picker.test.tsx` | 39 | 2 | 2 | 0 | Replaces newer HEAD content: applying wholesale drops 2 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=39, h_only=2). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub-picker.test.tsx` | 53 | 2 | 2 | 0 | Replaces newer HEAD content: applying wholesale drops 2 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=53, h_only=2). 1634 commits stale. |
| `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/resolve-stored-session.test.ts` | 49 | 1 | 1 | 0 | Replaces newer HEAD content: applying wholesale drops 1 non-blank HEAD line(s) HEAD added after the 2026-10-05 snapshot (b_only=49, h_only=1). 1634 commits stale. |

### Brand-token conflicts among the deferred

43 of the 84 deferred paths carry at least one Hermes-branded
branch-only line that HEAD does not have — i.e. the hazard named in
`docs/sync-w24/REPORT-w24.md` is present in over half the deferred set. These
would inject Hermes prose over HEAD's rebrand even if the content loss were
otherwise acceptable:

- `qubit_runtime/website/docs/developer-guide/relay-shared-metrics.md` — 33 Hermes branch-only line(s), 46 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/i18n.ts` — 13 Hermes branch-only line(s), 8 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/venv-holder-select.test.ts` — 11 Hermes branch-only line(s), 1 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/bootstrap-runner.ts` — 8 Hermes branch-only line(s), 59 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/app/chat/sidebar/profile-rail-fleet.test.tsx` — 8 Hermes branch-only line(s), 6 HEAD line(s) lost
- `qubit_runtime/plugin-catalog/README.md` — 8 Hermes branch-only line(s), 7 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/updater-process.ts` — 7 Hermes branch-only line(s), 16 HEAD line(s) lost
- `qubit_runtime/website/docs/getting-started/updating.md` — 7 Hermes branch-only line(s), 41 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/lib/voice-client-direct.ts` — 5 Hermes branch-only line(s), 15 HEAD line(s) lost
- `qubit_runtime/scripts/_activation.sh` — 5 Hermes branch-only line(s), 28 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/remote-ws-headers.test.ts` — 4 Hermes branch-only line(s), 8 HEAD line(s) lost
- `qubit_runtime/website/docs/guides/cron-script-only.md` — 4 Hermes branch-only line(s), 3 HEAD line(s) lost
- `qubit_runtime/website/docs/user-guide/configuration.md` — 4 Hermes branch-only line(s), 30 HEAD line(s) lost
- `qubit_runtime/website/docs/user-guide/features/plugin-catalog.md` — 4 Hermes branch-only line(s), 23 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/app/chat/right-rail/preview-pane.tsx` — 3 Hermes branch-only line(s), 22 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.test.ts` — 3 Hermes branch-only line(s), 17 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx` — 3 Hermes branch-only line(s), 55 HEAD line(s) lost
- `qubit_runtime/nix/checks.nix` — 3 Hermes branch-only line(s), 4 HEAD line(s) lost
- `qubit_runtime/website/docs/developer-guide/plugins/index.md` — 3 Hermes branch-only line(s), 15 HEAD line(s) lost
- `qubit_runtime/website/src/pages/plugins/index.tsx` — 3 Hermes branch-only line(s), 49 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/remote-lifecycle.test.ts` — 2 Hermes branch-only line(s), 3 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts` — 2 Hermes branch-only line(s), 15 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/app/right-sidebar/index.test.tsx` — 2 Hermes branch-only line(s), 4 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/sdk/index.ts` — 2 Hermes branch-only line(s), 6 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/store/projects.ts` — 2 Hermes branch-only line(s), 24 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/themes/backend-sync.ts` — 2 Hermes branch-only line(s), 12 HEAD line(s) lost
- `qubit_runtime/AGENTS.md` — 1 Hermes branch-only line(s), 9 HEAD line(s) lost
- `qubit_runtime/apps/bootstrap-installer/src-tauri/src/bootstrap.rs` — 1 Hermes branch-only line(s), 23 HEAD line(s) lost
- `qubit_runtime/apps/bootstrap-installer/src-tauri/src/lib.rs` — 1 Hermes branch-only line(s), 35 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron-builder.config.cjs` — 1 Hermes branch-only line(s), 4 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts` — 1 Hermes branch-only line(s), 25 HEAD line(s) lost
- `qubit_runtime/apps/desktop/electron/ssh-connection.ts` — 1 Hermes branch-only line(s), 15 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/status.ts` — 1 Hermes branch-only line(s), 8 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx` — 1 Hermes branch-only line(s), 36 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx` — 1 Hermes branch-only line(s), 52 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/lib/chat-messages/hydration.ts` — 1 Hermes branch-only line(s), 20 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub.tsx` — 1 Hermes branch-only line(s), 6 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/store/onboarding.ts` — 1 Hermes branch-only line(s), 24 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/store/updates.ts` — 1 Hermes branch-only line(s), 34 HEAD line(s) lost
- `qubit_runtime/apps/desktop/src/themes/context.tsx` — 1 Hermes branch-only line(s), 11 HEAD line(s) lost
- `qubit_runtime/website/docs/developer-guide/desktop-plugin-sdk.md` — 1 Hermes branch-only line(s), 11 HEAD line(s) lost
- `qubit_runtime/website/docs/user-guide/features/kanban.md` — 1 Hermes branch-only line(s), 8 HEAD line(s) lost
- `qubit_runtime/website/src/pages/skills/index.tsx` — 1 Hermes branch-only line(s), 9 HEAD line(s) lost

### Headline removals (largest HEAD content loss if taken)

- `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/utils.ts` — would drop **67** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/apps/desktop/src/app/context-menu/app-context-menu.tsx` — would drop **60** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/apps/desktop/electron/bootstrap-runner.ts` — would drop **59** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx` — would drop **55** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx` — would drop **52** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts` — would drop **50** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/website/src/pages/plugins/index.tsx` — would drop **49** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/website/docs/developer-guide/relay-shared-metrics.md` — would drop **46** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/website/docs/getting-started/updating.md` — would drop **41** HEAD line(s) HEAD added post-snapshot
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts` — would drop **39** HEAD line(s) HEAD added post-snapshot

---

## FAILED

None. Every candidate was either applied or cleanly deferred; no path errored.

## Verification

- `git status --short` after apply showed exactly the two intended paths.
- `backend-ready.ts`: `comm -23 <(HEAD lines) <(applied lines)` (non-blank) = **0** — no HEAD line lost.
- HEAD's exact Qubit reject line survives byte-identical in the applied file (`grep -F` = 1 hit).
- No `Hermes backend port` / `Hermes Desktop` / `Hermes Agent` prose in the applied file.
- `docs/SYNC_REMAINING.md`: absent from HEAD (`git cat-file -e HEAD:...` fails) — pure add.

### Not run (per repo cadence / environment)

- Full or package test suite: forbidden by `AGENTS.md` (implementation-first cadence;
  do not run the full pytest/Vitest sweep).
- `qubit_runtime/apps/desktop/electron/backend-ready.test.ts`: not runnable here —
  `node_modules/` is not installed in this worktree. Textual superset proof stands in.

## Artifacts

- `applied.json` — machine-readable applied list
- `deferred.json` — machine-readable deferred list
- `REPORT-w24-apply.md` — this file
