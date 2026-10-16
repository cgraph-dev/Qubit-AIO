# sync-w24 — per-file verdict report

**Analysis only. No branch content was applied to the tree.**

- **Branch analysed:** `sync-w24` @ `8efc0dd62043e3f1085b9900871e07b812389e51`
- **HEAD @** `74c4a8b91103a09f6cdf1a8a84293abf21df8f4f`
- **merge-base:** `fc88e7e227e23910a9270f3b99cf90883595c62e` (2026-10-05)
- **Files analysed:** 99 substantive files (branch adds >20 lines HEAD lacks)
- **Verdicts:** TAKE 85 · SKIP 0 · NEEDS-HUMAN 14
- **`w24_take.txt`:** 86 TAKE paths (the 85 above plus the clean-add `docs/SYNC_REMAINING.md`)

## What this branch is

`sync-w24` is a **single commit** (`8efc0dd62`, 2026-10-05 10:34) sitting on the merge-base `fc88e7e22`: an abandoned *upstream-sync* whose message is `sync(non-test): resolve 252 conflict files + docs/SYNC_REMAINING.md`. HEAD then advanced **1634 commits** from the same base without ever merging it. Branch-only content is therefore **upstream work HEAD never took** — or work HEAD independently re-implemented differently. Separating those two is the entire job of this lane.

## Method (reproduces the survey figures exactly: 225 / 1 / 99 / 9)

```sh
base=$(git merge-base HEAD sync-w24)
comm -23 <(git diff --name-only $base..sync-w24 | sort) \
         <(git diff --name-only $base..HEAD    | sort)   # 225 branch-only-changed files
```

For each of the 225, and for each path that also exists in HEAD:

- `b_minus_h` = number of distinct lines the branch has that HEAD lacks.
- `h_minus_b` = number of distinct lines HEAD has that the branch lacks.
- A path absent from HEAD is a clean add (cannot conflict).

Bucket totals: **225** branch-only-changed · **1** absent from HEAD (`docs/SYNC_REMAINING.md`) · **99** substantive (`b_minus_h > 20`, analysed below) · **9** superseded (`b_minus_h == 0`).

### Verdict rule

- **TAKE** — branch-only lines are coherent content HEAD genuinely lacks.
- **SKIP** — HEAD already superseded the branch's content (see note: none qualified within the 99).
- **NEEDS-HUMAN** — the file is an i18n locale bundle with an independent translation set, or both sides evolved the file heavily so a straight take would be a real merge, or HEAD holds a complete different implementation of the same feature.

## Per-file verdicts

| # | path | b_minus_h | h_minus_b | verdict | evidence |
|---|------|----------:|----------:|---------|----------|
| 1 | `qubit_runtime/AGENTS.md` | 25 | 9 | **TAKE** | new syms: `Undisclosed` |
| 2 | `qubit_runtime/apps/bootstrap-installer/src-tauri/src/bootstrap.rs` | 104 | 23 | **TAKE** | issues absent from HEAD tree: #94703 ; new syms: `Gregorian` |
| 3 | `qubit_runtime/apps/bootstrap-installer/src-tauri/src/lib.rs` | 45 | 35 | **TAKE** | branch adds 45 lines HEAD lacks; HEAD-only=35 |
| 4 | `qubit_runtime/apps/desktop/electron-builder.config.cjs` | 26 | 4 | **TAKE** | branch adds 26 lines HEAD lacks; HEAD-only=4 |
| 5 | `qubit_runtime/apps/desktop/electron/backend-ready.ts` | 54 | 0 | **TAKE** | issues absent from HEAD tree: #122206 ; new syms: `SOURCE_COMPLETION_BANNER_RE`, `SOURCE_COMPLETION_GRACE_MS`, `SOURCE_COMPLETION_MAX_TOTAL_MS` |
| 6 | `qubit_runtime/apps/desktop/electron/bootstrap-runner.test.ts` | 28 | 33 | **TAKE** | branch adds 28 lines HEAD lacks; HEAD-only=33 |
| 7 | `qubit_runtime/apps/desktop/electron/bootstrap-runner.ts` | 43 | 59 | **TAKE** | branch adds 43 lines HEAD lacks; HEAD-only=59 |
| 8 | `qubit_runtime/apps/desktop/electron/entry.ts` | 34 | 2 | **TAKE** | branch adds 34 lines HEAD lacks; HEAD-only=2 |
| 9 | `qubit_runtime/apps/desktop/electron/main.ts` | 1886 | 934 | **NEEDS-HUMAN** | issues absent from HEAD tree: #101683, #102451, #102782, #103288, #105239, #108047 ; new syms: `FILE_REVEAL_DEDUPE_MS`, `GUARD_REPORT_DEPS`, `HERMES_DESKTOP_RENDERER_ACCESSIBILITY`, `LINUX_GPU_SOFTWARE_ACTIVE` |
| 10 | `qubit_runtime/apps/desktop/electron/minimize-to-tray.ts` | 41 | 7 | **TAKE** | issues absent from HEAD tree: #126353, #126570, #127349 ; new syms: `StatusNotifierItem` |
| 11 | `qubit_runtime/apps/desktop/electron/remote-lifecycle.test.ts` | 127 | 3 | **TAKE** | issues absent from HEAD tree: #124617, #88842, #94642 ; new syms: `MIN_READY_TIMEOUT_MS`, `QUBIT_DESKTOP_REMOTE_READY_TIMEOUT_MS` |
| 12 | `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts` | 82 | 25 | **TAKE** | issues absent from HEAD tree: #88842, #94642 ; new syms: `MIN_READY_TIMEOUT_MS`, `QUBIT_DESKTOP_REMOTE_READY_TIMEOUT_MS` |
| 13 | `qubit_runtime/apps/desktop/electron/remote-ws-headers.test.ts` | 57 | 8 | **TAKE** | branch adds 57 lines HEAD lacks; HEAD-only=8 |
| 14 | `qubit_runtime/apps/desktop/electron/ssh-connection.ts` | 85 | 15 | **TAKE** | issues absent from HEAD tree: #97264 |
| 15 | `qubit_runtime/apps/desktop/electron/update-handoff-marker.test.ts` | 31 | 18 | **TAKE** | issues absent from HEAD tree: #122130 |
| 16 | `qubit_runtime/apps/desktop/electron/updater-process.ts` | 30 | 16 | **TAKE** | branch adds 30 lines HEAD lacks; HEAD-only=16 |
| 17 | `qubit_runtime/apps/desktop/electron/venv-holder-select.test.ts` | 23 | 1 | **TAKE** | issues absent from HEAD tree: #62311, #62445 |
| 18 | `qubit_runtime/apps/desktop/src/app/artifacts/artifact-utils.ts` | 38 | 12 | **TAKE** | new syms: `ARTIFACT_MESSAGE_PAGE_SIZE`, `MAX_ARTIFACT_MESSAGE_PAGE_JSON_CHARS` |
| 19 | `qubit_runtime/apps/desktop/src/app/chat/composer/hooks/use-composer-drop.ts` | 23 | 4 | **TAKE** | issues absent from HEAD tree: #52427 |
| 20 | `qubit_runtime/apps/desktop/src/app/chat/right-rail/preview-pane.tsx` | 113 | 22 | **TAKE** | issues absent from HEAD tree: #101880, #120265 ; new syms: `Steers` |
| 21 | `qubit_runtime/apps/desktop/src/app/chat/right-rail/real-profile-consent-dialog.tsx` | 42 | 7 | **TAKE** | issues absent from HEAD tree: #119398 ; new syms: `RealProfilePromptGate` |
| 22 | `qubit_runtime/apps/desktop/src/app/chat/session-tile.tsx` | 158 | 32 | **TAKE** | issues absent from HEAD tree: #125678 |
| 23 | `qubit_runtime/apps/desktop/src/app/chat/sidebar/profile-rail-fleet.test.tsx` | 99 | 6 | **TAKE** | issues absent from HEAD tree: #102826 |
| 24 | `qubit_runtime/apps/desktop/src/app/chat/sidebar/profile-switcher.tsx` | 149 | 101 | **NEEDS-HUMAN** | both sides evolved heavily (branch-only=149, HEAD-only=101); needs a real merge, not a take |
| 25 | `qubit_runtime/apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts` | 24 | 2 | **TAKE** | issues absent from HEAD tree: #108694, #61362 ; new syms: `QubitGitBranch`, `QubitGitWorktree` |
| 26 | `qubit_runtime/apps/desktop/src/app/chat/transcript-backfill.test.ts` | 141 | 4 | **TAKE** | issues absent from HEAD tree: #123909, #124311, #65047, #96875 |
| 27 | `qubit_runtime/apps/desktop/src/app/chat/transcript-backfill.ts` | 248 | 14 | **TAKE** | issues absent from HEAD tree: #96875 ; new syms: `REFRESH_OVERLAP_PAGE_LIMIT` |
| 28 | `qubit_runtime/apps/desktop/src/app/command-center/index.tsx` | 190 | 147 | **NEEDS-HUMAN** | both sides evolved heavily (branch-only=190, HEAD-only=147); needs a real merge, not a take |
| 29 | `qubit_runtime/apps/desktop/src/app/context-menu/app-context-menu.tsx` | 26 | 60 | **TAKE** | branch adds 26 lines HEAD lacks; HEAD-only=60 |
| 30 | `qubit_runtime/apps/desktop/src/app/contrib/hooks/completed-reply-refresh.test.tsx` | 66 | 4 | **TAKE** | branch adds 66 lines HEAD lacks; HEAD-only=4 |
| 31 | `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-background-sync.test.ts` | 113 | 7 | **TAKE** | issues absent from HEAD tree: #123622, #125532, #53046, #56757, #95767 ; new syms: `TRANSCRIPT_RETURN_REFRESH_MIN_GAP_MS` |
| 32 | `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-background-sync.ts` | 189 | 24 | **TAKE** | issues absent from HEAD tree: #123622, #125532, #126422, #53046, #56757, #65047 ; new syms: `TRANSCRIPT_RETURN_REFRESH_MIN_GAP_MS` |
| 33 | `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts` | 82 | 15 | **TAKE** | issues absent from HEAD tree: #56983, #65457, #74948 |
| 34 | `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.test.ts` | 181 | 13 | **TAKE** | issues absent from HEAD tree: #88443 |
| 35 | `qubit_runtime/apps/desktop/src/app/profiles/index.test.tsx` | 24 | 4 | **TAKE** | branch adds 24 lines HEAD lacks; HEAD-only=4 |
| 36 | `qubit_runtime/apps/desktop/src/app/right-sidebar/files/ipc.ts` | 73 | 10 | **TAKE** | issues absent from HEAD tree: #55169 ; new syms: `QubitReadDirEntry` |
| 37 | `qubit_runtime/apps/desktop/src/app/right-sidebar/index.test.tsx` | 37 | 4 | **TAKE** | branch adds 37 lines HEAD lacks; HEAD-only=4 |
| 38 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/status.ts` | 30 | 8 | **TAKE** | issues absent from HEAD tree: #97635 |
| 39 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx` | 237 | 36 | **TAKE** | issues absent from HEAD tree: #105176, #105256, #42093, #81798, #88443, #97635 ; new syms: `SLASH_METRIC` |
| 40 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts` | 64 | 39 | **TAKE** | issues absent from HEAD tree: #105176, #107593, #68734 |
| 41 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts` | 83 | 15 | **TAKE** | issues absent from HEAD tree: #105256, #123337, #57444, #94001, #97635 |
| 42 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts` | 131 | 50 | **TAKE** | issues absent from HEAD tree: #124005, #47709, #63682, #77078, #88443 |
| 43 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/index.ts` | 480 | 103 | **NEEDS-HUMAN** | issues absent from HEAD tree: #124265, #52589, #65410, #66057, #73890, #75587 ; new syms: `CREATE_GUARD_RELEASE_MS`, `UNLATCHED` |
| 44 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/resolve-stored-session.test.ts` | 49 | 1 | **TAKE** | issues absent from HEAD tree: #85163 |
| 45 | `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/utils.ts` | 301 | 67 | **TAKE** | issues absent from HEAD tree: #120978, #121088, #121613, #122079, #123047, #125336 |
| 46 | `qubit_runtime/apps/desktop/src/app/settings/helpers.ts` | 27 | 4 | **TAKE** | issues absent from HEAD tree: #123297 |
| 47 | `qubit_runtime/apps/desktop/src/app/settings/terminal-backend-panel.test.tsx` | 21 | 5 | **TAKE** | branch adds 21 lines HEAD lacks; HEAD-only=5 |
| 48 | `qubit_runtime/apps/desktop/src/app/shell/model-catalog-menu.tsx` | 426 | 130 | **NEEDS-HUMAN** | issues absent from HEAD tree: #123040, #130349, #53980, #63125, #86966 ; new syms: `DirectionProvider`, `HIGHLIGHTED` |
| 49 | `qubit_runtime/apps/desktop/src/components/assistant-ui/directive-text.tsx` | 29 | 15 | **TAKE** | new syms: `BLOB_MARKDOWN_IMAGE_RE` |
| 50 | `qubit_runtime/apps/desktop/src/components/assistant-ui/embeds/social-embed.tsx` | 73 | 80 | **NEEDS-HUMAN** | new syms: `EmbedMessage`, `FRAME_ORIGIN`, `SOCIAL_FRAME_SANDBOX` |
| 51 | `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.test.ts` | 118 | 17 | **TAKE** | issues absent from HEAD tree: #118973, #123048 |
| 52 | `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx` | 141 | 52 | **TAKE** | issues absent from HEAD tree: #118973, #123048, #95814 ; new syms: `INTENT_ACK_MESSAGE_TYPE`, `INTENT_ACK_TIMEOUT_MS`, `IntentAck`, `IntentDecision` |
| 53 | `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx` | 127 | 55 | **TAKE** | issues absent from HEAD tree: #118864, #119080, #126486 |
| 54 | `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/use-message-reactions.ts` | 38 | 10 | **TAKE** | branch adds 38 lines HEAD lacks; HEAD-only=10 |
| 55 | `qubit_runtime/apps/desktop/src/components/chat/shiki-highlighter.tsx` | 37 | 7 | **TAKE** | issues absent from HEAD tree: #95995 ; new syms: `QubitSyntaxHighlighterProps` |
| 56 | `qubit_runtime/apps/desktop/src/components/language-switcher.test.tsx` | 35 | 2 | **TAKE** | branch adds 35 lines HEAD lacks; HEAD-only=2 |
| 57 | `qubit_runtime/apps/desktop/src/i18n/de.ts` | 167 | 34 | **NEEDS-HUMAN** | new syms: `Bearbeitungen`, `Begrenzte`, `Durchgang`, `Durchlauf` |
| 58 | `qubit_runtime/apps/desktop/src/i18n/en.ts` | 213 | 57 | **NEEDS-HUMAN** | i18n locale bundle: independent translation sets (merge is manual, not a straight take) |
| 59 | `qubit_runtime/apps/desktop/src/i18n/es.ts` | 167 | 36 | **NEEDS-HUMAN** | new syms: `Contadores`, `Enfocado`, `Favoritos`, `Inactivos` |
| 60 | `qubit_runtime/apps/desktop/src/i18n/fr.ts` | 170 | 36 | **NEEDS-HUMAN** | new syms: `Augmenter`, `Compteurs`, `Favoris`, `Inactifs` |
| 61 | `qubit_runtime/apps/desktop/src/i18n/ja.ts` | 160 | 22 | **NEEDS-HUMAN** | i18n locale bundle: independent translation sets (merge is manual, not a straight take) |
| 62 | `qubit_runtime/apps/desktop/src/i18n/ru.ts` | 147 | 23 | **NEEDS-HUMAN** | i18n locale bundle: independent translation sets (merge is manual, not a straight take) |
| 63 | `qubit_runtime/apps/desktop/src/i18n/zh.ts` | 146 | 24 | **NEEDS-HUMAN** | i18n locale bundle: independent translation sets (merge is manual, not a straight take) |
| 64 | `qubit_runtime/apps/desktop/src/lib/chat-messages/hydration.ts` | 88 | 20 | **TAKE** | new syms: `LEGACY_HEARTBEAT_ROW_RE` |
| 65 | `qubit_runtime/apps/desktop/src/lib/desktop-fs.ts` | 35 | 8 | **TAKE** | new syms: `QubitReadFileErrorResult`, `QubitReadFileTextResult`, `QubitSelectPathsOptions` |
| 66 | `qubit_runtime/apps/desktop/src/lib/stale-transcript-guard.ts` | 101 | 13 | **TAKE** | issues absent from HEAD tree: #123909, #124005, #125975 |
| 67 | `qubit_runtime/apps/desktop/src/lib/voice-client-direct.ts` | 52 | 15 | **TAKE** | new syms: `VoiceConfigResponse` |
| 68 | `qubit_runtime/apps/desktop/src/plugins/hermes-bots/i18n.ts` | 62 | 8 | **TAKE** | branch adds 62 lines HEAD lacks; HEAD-only=8 |
| 69 | `qubit_runtime/apps/desktop/src/plugins/hermes-bots/model-picker.test.tsx` | 39 | 2 | **TAKE** | issues absent from HEAD tree: #121875 |
| 70 | `qubit_runtime/apps/desktop/src/plugins/hermes-bots/relay.test.ts` | 103 | 6 | **TAKE** | issues absent from HEAD tree: #93911 |
| 71 | `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub-picker.test.tsx` | 53 | 2 | **TAKE** | issues absent from HEAD tree: #91612 |
| 72 | `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub.tsx` | 46 | 6 | **TAKE** | issues absent from HEAD tree: #118203, #91612 ; new syms: `HUB_PROBE_TIMEOUT_MS` |
| 73 | `qubit_runtime/apps/desktop/src/sdk/index.ts` | 89 | 6 | **TAKE** | issues absent from HEAD tree: #60654 ; new syms: `HAPPENED` |
| 74 | `qubit_runtime/apps/desktop/src/store/onboarding.ts` | 118 | 24 | **TAKE** | issues absent from HEAD tree: #124939 ; new syms: `BOOT_RACE_GRACE_MS` |
| 75 | `qubit_runtime/apps/desktop/src/store/projects.test.ts` | 100 | 11 | **TAKE** | issues absent from HEAD tree: #123337 |
| 76 | `qubit_runtime/apps/desktop/src/store/projects.ts` | 92 | 24 | **TAKE** | issues absent from HEAD tree: #123337, #123685 ; new syms: `QubitGitBranch` |
| 77 | `qubit_runtime/apps/desktop/src/store/reactions.test.ts` | 62 | 3 | **TAKE** | branch adds 62 lines HEAD lacks; HEAD-only=3 |
| 78 | `qubit_runtime/apps/desktop/src/store/review.test.ts` | 146 | 3 | **TAKE** | new syms: `MAX_TURN_BASES`, `QubitReviewShipInfo` |
| 79 | `qubit_runtime/apps/desktop/src/store/review.ts` | 126 | 29 | **TAKE** | new syms: `MAX_TURN_BASES`, `QubitReviewShipInfo` |
| 80 | `qubit_runtime/apps/desktop/src/store/session-states.ts` | 502 | 112 | **NEEDS-HUMAN** | issues absent from HEAD tree: #103123, #108679, #125372, #125899, #73890, #98622 ; new syms: `NO_REPLY_ERROR`, `NO_REPLY_SURFACE` |
| 81 | `qubit_runtime/apps/desktop/src/store/session.test.ts` | 140 | 3 | **TAKE** | issues absent from HEAD tree: #118156, #123337, #123685, #85331, #96834 |
| 82 | `qubit_runtime/apps/desktop/src/store/session.ts` | 131 | 27 | **TAKE** | issues absent from HEAD tree: #103123, #118156, #123337, #52589, #85331, #94124 |
| 83 | `qubit_runtime/apps/desktop/src/store/updates.ts` | 119 | 34 | **TAKE** | new syms: `GUI_SKEW_TOAST_ID`, `GUI_SKEW_TOAST_SNOOZE_KEY` |
| 84 | `qubit_runtime/apps/desktop/src/themes/backend-sync.ts` | 47 | 12 | **TAKE** | issues absent from HEAD tree: #130015 ; new syms: `QubitSkin` |
| 85 | `qubit_runtime/apps/desktop/src/themes/context.tsx` | 40 | 11 | **TAKE** | branch adds 40 lines HEAD lacks; HEAD-only=11 |
| 86 | `qubit_runtime/nix/checks.nix` | 25 | 4 | **TAKE** | branch adds 25 lines HEAD lacks; HEAD-only=4 |
| 87 | `qubit_runtime/plugin-catalog/README.md` | 53 | 7 | **TAKE** | branch adds 53 lines HEAD lacks; HEAD-only=7 |
| 88 | `qubit_runtime/scripts/_activation.sh` | 27 | 28 | **TAKE** | branch adds 27 lines HEAD lacks; HEAD-only=28 |
| 89 | `qubit_runtime/website/docs/developer-guide/context-compression-and-caching.md` | 27 | 14 | **TAKE** | issues absent from HEAD tree: #123167 |
| 90 | `qubit_runtime/website/docs/developer-guide/desktop-plugin-sdk.md` | 141 | 11 | **TAKE** | new syms: `PROVIDER_ICONS`, `QubitOpenTarget`, `QubitPlugin` |
| 91 | `qubit_runtime/website/docs/developer-guide/plugins/index.md` | 56 | 15 | **TAKE** | branch adds 56 lines HEAD lacks; HEAD-only=15 |
| 92 | `qubit_runtime/website/docs/developer-guide/relay-shared-metrics.md` | 268 | 46 | **TAKE** | new syms: `Vocabularies` |
| 93 | `qubit_runtime/website/docs/getting-started/updating.md` | 48 | 41 | **TAKE** | branch adds 48 lines HEAD lacks; HEAD-only=41 |
| 94 | `qubit_runtime/website/docs/guides/cron-script-only.md` | 23 | 3 | **TAKE** | branch adds 23 lines HEAD lacks; HEAD-only=3 |
| 95 | `qubit_runtime/website/docs/user-guide/configuration.md` | 46 | 30 | **TAKE** | branch adds 46 lines HEAD lacks; HEAD-only=30 |
| 96 | `qubit_runtime/website/docs/user-guide/features/kanban.md` | 24 | 8 | **TAKE** | branch adds 24 lines HEAD lacks; HEAD-only=8 |
| 97 | `qubit_runtime/website/docs/user-guide/features/plugin-catalog.md` | 34 | 23 | **TAKE** | branch adds 34 lines HEAD lacks; HEAD-only=23 |
| 98 | `qubit_runtime/website/src/pages/plugins/index.tsx` | 68 | 49 | **TAKE** | branch adds 68 lines HEAD lacks; HEAD-only=49 |
| 99 | `qubit_runtime/website/src/pages/skills/index.tsx` | 68 | 9 | **TAKE** | issues absent from HEAD tree: #97861 ; new syms: `CATALOG_STALL_TIMEOUT_MS` |

## TAKE list (85 files in the 99)

- `qubit_runtime/AGENTS.md`
- `qubit_runtime/apps/bootstrap-installer/src-tauri/src/bootstrap.rs`
- `qubit_runtime/apps/bootstrap-installer/src-tauri/src/lib.rs`
- `qubit_runtime/apps/desktop/electron-builder.config.cjs`
- `qubit_runtime/apps/desktop/electron/backend-ready.ts`
- `qubit_runtime/apps/desktop/electron/bootstrap-runner.test.ts`
- `qubit_runtime/apps/desktop/electron/bootstrap-runner.ts`
- `qubit_runtime/apps/desktop/electron/entry.ts`
- `qubit_runtime/apps/desktop/electron/minimize-to-tray.ts`
- `qubit_runtime/apps/desktop/electron/remote-lifecycle.test.ts`
- `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts`
- `qubit_runtime/apps/desktop/electron/remote-ws-headers.test.ts`
- `qubit_runtime/apps/desktop/electron/ssh-connection.ts`
- `qubit_runtime/apps/desktop/electron/update-handoff-marker.test.ts`
- `qubit_runtime/apps/desktop/electron/updater-process.ts`
- `qubit_runtime/apps/desktop/electron/venv-holder-select.test.ts`
- `qubit_runtime/apps/desktop/src/app/artifacts/artifact-utils.ts`
- `qubit_runtime/apps/desktop/src/app/chat/composer/hooks/use-composer-drop.ts`
- `qubit_runtime/apps/desktop/src/app/chat/right-rail/preview-pane.tsx`
- `qubit_runtime/apps/desktop/src/app/chat/right-rail/real-profile-consent-dialog.tsx`
- `qubit_runtime/apps/desktop/src/app/chat/session-tile.tsx`
- `qubit_runtime/apps/desktop/src/app/chat/sidebar/profile-rail-fleet.test.tsx`
- `qubit_runtime/apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts`
- `qubit_runtime/apps/desktop/src/app/chat/transcript-backfill.test.ts`
- `qubit_runtime/apps/desktop/src/app/chat/transcript-backfill.ts`
- `qubit_runtime/apps/desktop/src/app/context-menu/app-context-menu.tsx`
- `qubit_runtime/apps/desktop/src/app/contrib/hooks/completed-reply-refresh.test.tsx`
- `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-background-sync.test.ts`
- `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-background-sync.ts`
- `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts`
- `qubit_runtime/apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.test.ts`
- `qubit_runtime/apps/desktop/src/app/profiles/index.test.tsx`
- `qubit_runtime/apps/desktop/src/app/right-sidebar/files/ipc.ts`
- `qubit_runtime/apps/desktop/src/app/right-sidebar/index.test.tsx`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/status.ts`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/resolve-stored-session.test.ts`
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/utils.ts`
- `qubit_runtime/apps/desktop/src/app/settings/helpers.ts`
- `qubit_runtime/apps/desktop/src/app/settings/terminal-backend-panel.test.tsx`
- `qubit_runtime/apps/desktop/src/components/assistant-ui/directive-text.tsx`
- `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.test.ts`
- `qubit_runtime/apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx`
- `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx`
- `qubit_runtime/apps/desktop/src/components/assistant-ui/thread/use-message-reactions.ts`
- `qubit_runtime/apps/desktop/src/components/chat/shiki-highlighter.tsx`
- `qubit_runtime/apps/desktop/src/components/language-switcher.test.tsx`
- `qubit_runtime/apps/desktop/src/lib/chat-messages/hydration.ts`
- `qubit_runtime/apps/desktop/src/lib/desktop-fs.ts`
- `qubit_runtime/apps/desktop/src/lib/stale-transcript-guard.ts`
- `qubit_runtime/apps/desktop/src/lib/voice-client-direct.ts`
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/i18n.ts`
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/model-picker.test.tsx`
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/relay.test.ts`
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub-picker.test.tsx`
- `qubit_runtime/apps/desktop/src/plugins/hermes-bots/skills-hub.tsx`
- `qubit_runtime/apps/desktop/src/sdk/index.ts`
- `qubit_runtime/apps/desktop/src/store/onboarding.ts`
- `qubit_runtime/apps/desktop/src/store/projects.test.ts`
- `qubit_runtime/apps/desktop/src/store/projects.ts`
- `qubit_runtime/apps/desktop/src/store/reactions.test.ts`
- `qubit_runtime/apps/desktop/src/store/review.test.ts`
- `qubit_runtime/apps/desktop/src/store/review.ts`
- `qubit_runtime/apps/desktop/src/store/session.test.ts`
- `qubit_runtime/apps/desktop/src/store/session.ts`
- `qubit_runtime/apps/desktop/src/store/updates.ts`
- `qubit_runtime/apps/desktop/src/themes/backend-sync.ts`
- `qubit_runtime/apps/desktop/src/themes/context.tsx`
- `qubit_runtime/nix/checks.nix`
- `qubit_runtime/plugin-catalog/README.md`
- `qubit_runtime/scripts/_activation.sh`
- `qubit_runtime/website/docs/developer-guide/context-compression-and-caching.md`
- `qubit_runtime/website/docs/developer-guide/desktop-plugin-sdk.md`
- `qubit_runtime/website/docs/developer-guide/plugins/index.md`
- `qubit_runtime/website/docs/developer-guide/relay-shared-metrics.md`
- `qubit_runtime/website/docs/getting-started/updating.md`
- `qubit_runtime/website/docs/guides/cron-script-only.md`
- `qubit_runtime/website/docs/user-guide/configuration.md`
- `qubit_runtime/website/docs/user-guide/features/kanban.md`
- `qubit_runtime/website/docs/user-guide/features/plugin-catalog.md`
- `qubit_runtime/website/src/pages/plugins/index.tsx`
- `qubit_runtime/website/src/pages/skills/index.tsx`

Plus the clean add (absent from HEAD entirely):

- `docs/SYNC_REMAINING.md`

## NEEDS-HUMAN list (14 files)

- `qubit_runtime/apps/desktop/electron/main.ts` — both sides evolved heavily (branch-only=1886, HEAD-only=934); needs a real merge, not a take
- `qubit_runtime/apps/desktop/src/app/chat/sidebar/profile-switcher.tsx` — both sides evolved heavily (branch-only=149, HEAD-only=101); needs a real merge, not a take
- `qubit_runtime/apps/desktop/src/app/command-center/index.tsx` — both sides evolved heavily (branch-only=190, HEAD-only=147); needs a real merge, not a take
- `qubit_runtime/apps/desktop/src/app/session/hooks/use-session-actions/index.ts` — both sides evolved heavily (branch-only=480, HEAD-only=103); needs a real merge, not a take
- `qubit_runtime/apps/desktop/src/app/shell/model-catalog-menu.tsx` — both sides evolved heavily (branch-only=426, HEAD-only=130); needs a real merge, not a take
- `qubit_runtime/apps/desktop/src/components/assistant-ui/embeds/social-embed.tsx` — HEAD implements the same embeds differently (blockquote+widget script vs sandboxed iframe); two complete implementations of one feature
- `qubit_runtime/apps/desktop/src/i18n/de.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/i18n/en.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/i18n/es.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/i18n/fr.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/i18n/ja.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/i18n/ru.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/i18n/zh.ts` — i18n locale bundle: independent translation sets (merge is manual, not a straight take)
- `qubit_runtime/apps/desktop/src/store/session-states.ts` — both sides evolved heavily (branch-only=502, HEAD-only=112); needs a real merge, not a take

## Notes and caveats

- **No SKIP verdicts inside the 99.** Every substantive file contains coherent branch-only lines HEAD does not have; none is a strict subset of HEAD. The 9 truly superseded files have `b_minus_h == 0` and were excluded by the `>20` filter. A near-duplicate scan (normalized whitespace) found no file above a 0.70 near-duplicate fraction.
- **Rebrand is mixed — this is the real hazard.** The branch is a rebrand-in-progress snapshot: its branch-only lines are predominantly `Qubit`-branded, but HEAD still carries `Hermes`-branded lines in many of the same files (and vice-versa in the i18n bundles). A blind `git checkout sync-w24 -- <path>` would revert HEAD's independent rebrand in the affected files. **TAKE means the content is worth having; it does not mean the file is safe to take wholesale without a rebrand pass.**
- **NEEDS-HUMAN rationale.** `main.ts` (1886/934), `session-states.ts` (502/112), `model-catalog-menu.tsx` (426/130), `use-session-actions/index.ts` (480/103), `command-center/index.tsx` (190/147), `profile-switcher.tsx` (149/101) both diverged heavily — a real 3-way merge is required. `social-embed.tsx` has two complete implementations of the same feature (HEAD: blockquote + widget script; branch: sandboxed iframe) — a product call. The 7 locale bundles (`de/es/fr/ja/ru/zh/en`) carry independent translation sets and a new `sharedMetrics` consent block; merging them is a translation task, not a code take.
- **Method limits.** Token-level symbol/issue detection under-counts genuine additions (verified on files with `only_branch == 0` such as `profile-switcher.tsx` and `updater-process.ts`, which still contain large coherent new code). Verdicts were confirmed by direct diff inspection on a sample: `main.ts`, `session-states.ts`, `backend-ready.ts`, `updates.ts`, `transcript-backfill.ts`, `profile-switcher.tsx`, `social-embed.tsx`, `i18n/*`.
- **Not applied.** No branch content was merged or checked out. This report and the take list are the deliverable.

