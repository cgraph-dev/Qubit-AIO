# REPORT — desktop/Electron parity for the 1:1 re-pin

Lane: `port/desktop-parity`, worktree `/home/trick/qubit-worktrees/port-9`,
base `main` `d92b51685`.
Upstream: `/home/trick/.hermes.pre-retirement/hermes-agent` at
`088f657ecf77674c7b8e2ededf4e3576d82e042c`.
Local desktop: `qubit_runtime/apps/desktop`.
Method: file-list measurement, then targeted newer-file ports with Qubit
adaptations. No service restart, no push, no `npm install`, `frontend/qubit-web`
and `frontend/qubit-dist` untouched.

## 1. Measured gap

Commands (generated/dist/node_modules excluded):

```bash
UP=/home/trick/.hermes.pre-retirement/hermes-agent
git -C "$UP" ls-tree -r --name-only 088f657ecf apps/desktop | sed 's#^apps/desktop/##' | sort
find qubit_runtime/apps/desktop -type f -not -path '*/node_modules/*' -not -path '*/dist*' | sort
```

| Measure | Value |
|---|---|
| Upstream files at the re-pin | **4,603** |
| Local files at the base | **3,749** |
| Missing locally (before this lane) | **1,001** |
| — `src/` | 844 |
| — `electron/` | 121 |
| — `e2e/` / `scripts/` / `docs/` / other | 19 / 12 / 3 / 2 |
| Old-pin → re-pin changes | **1,244** (530 added, 694 modified, 19 deleted, 1 renamed) |

So the missing files are not only re-pin additions: the local tree was already
**472 files behind the old pin** (397 `src`, 58 `electron`, 8 `e2e`, 4
`scripts`, …). The desktop tree is a hybrid of several older snapshots, which
is why renderer `tsc -p . --noEmit` reports **421** pre-existing errors and
`vite build` already fails on the base (see §4).

## 2. What was ported

Three commits, all under `qubit_runtime/apps/desktop/electron`:

1. `b6d395396` — **re-pin electron modules** (56 files). The self-contained
   modules added at `088f657`: plugin file grants/hardening (5 files + 4
   tests), UI preferences, relaunch target/sandbox relaunch, Linux NVIDIA EGL
   recovery, keep-awake controller, OAuth login window/flights, save-text-file,
   spellcheck dictionary IPC, update proxy hosts, launch build identity,
   packaged working directory, profile routes, registry backend identity,
   update stop remedy, URL filename, Windows runtime command, plus
   `local-gateway.ts`. Also the additive `power-save` KeepAwakeMode surface,
   the Linux GPU/EGL fallback changes those modules need, and one `preload.ts`
   line forwarding the window's connection/profile to `hermes:version`.
   Qubit adaptations: version banner accepts `Qubit Agent` and `Hermes Agent`;
   OAuth/UI copy says Qubit; `ui-preferences` tests use `QUBIT_HOME` (the
   Python twin reads it); GPU override helper keeps the local
   `isQubitDesktopGpuOverrideOff` name.
2. `d4ebec852` — **clean electron updates** (7 files): renderer heap flags
   (`applyLaunchSwitches`), renderer log parsing, window connection routing
   (`versionScopeFor` / `windowRouteVersionScope`), and the app-version /
   desktop-electron-pin / launch-profile tests.
3. `385c8bbc0` — **modules missing since the old pin** (35 files): backend
   descriptor cache, backend spawn env, challenge window, deep-link protocol,
   system CA, local-gateway Python ticket bridge + helpers, prepared
   submissions, preview guest throttle, update hold types, update marker
   gate/judge/helpers, remote update marker programs, remote lifecycle
   helpers, window size types.

Net: **87 electron files added, 11 modified**. Electron missing dropped
**121 → 34**.

## 3. Qubit preservation and Electron 44 plan

Local fixes kept, not replaced wholesale: the whole `src/` tree is untouched;
only files whose local copy was byte-identical to the old pin were upgraded
verbatim, and newer modules were added without rewiring `main.ts`.
`docs/ELECTRON-44-MIGRATION.md` is now in the tree (the plan already existed on
the desktop lane). Electron **44 is not installed**, as instructed.

Remaining Electron 44 / castlabs blockers (unchanged, from that doc):

- Stock Electron 44 has no Widevine `components` API; DRM needs castlabs
  `44.9.3+wvcus`.
- Packaging acquires stock Electron via `downloadElectronArtifactZip`; it must
  ship the castlabs dist instead.
- The version guard regex rejects the castlabs git spec; the pin test must
  compare the numeric tag + lock commit.
- Native ABI 143 → 149 (`node-pty`, `get-windows`), macOS 13 floor, async
  main-process clipboard, `display-capture` permission, no 32-bit targets,
  static ANGLE, OOPIF PDFs, and VMP signing/notice requirements.

## 4. Verification

| Check | Result |
|---|---|
| `tsc -p tsconfig.electron.json --noEmit` | **20 errors before, 20 after** (same files) |
| New/ported electron focused tests | **254 passed, 1 skipped** (23 files) |
| Second batch | **30 passed** (4 files) |
| Third batch | **95 passed, 2 skipped** (17 files) |
| `npx --no-install vite build` | **exit 1 on the base and after this lane** |

The build result is honest and pre-existing: the base fails with exactly one
error, `Could not resolve './submit-shortcut' in
src/components/assistant-ui/clarify/pending.tsx`; adding that file merely
reveals the next layer of the 844-file `src` gap (missing exports in
`@/global`, `@/store/session`, `@/api/client`, `@qubit/plugin-sdk`, etc. —
421 renderer `tsc` errors measured). Making the renderer build green requires
the full `src` sync (or the Qubit desktop lane), not a desktop-electron port,
so it was not faked with stubs.

The QubitTV 20-file suite is **not present on this base** (the native player
lives on another branch; this tree has no `electron/player` and no
`qubit_tv_*` desktop tests). Nothing under `frontend/qubit-web` was touched, so
the web QubitTV page/tests are unaffected.

## 5. Remaining, with reasons

**34 electron files** still missing:

- `clipboard-ipc.ts` — written for Electron 44's async clipboard API; local is
  Electron 40.10.2 (`writeText` returns `string`). Blocked on the Electron 44
  upgrade.
- `local-runtime-version.ts` (+test) — needs
  `updater-process.resolveInstallationLauncherAsync`, whose support helpers
  (`realpathOrNull`, `launcherInstallDirectoryAsync`,
  `legacyInstallationLauncher`) are not in the local `updater-process`.
- `release-holder-consent.ts` (+test) and `update-handoff-report.ts` (+test) —
  need the newer `handoff-result` (`ReleaseHolder`, `warnings`,
  `releaseHolders`, `runId`) which `main.ts` also consumes.
- `gateway-download-transport.ts` (+test) — needs
  `gateway-file-download.destroyStalledBody`.
- `update-hold-wiring.ts`, `update-marker-gate.ts`, `update-marker.test-helpers.ts`
  (+tests) — need `update-marker` `CreateTimeProbe`, `inspectUpdateMarker`,
  `MarkerInspection`, `cachedCreateTimeProbe`, `formatCreateTime`.
- `windows-remote-inventory.ts` (+test), `windows-ssh-repair.test.ts` — need
  the newer `windows-remote-lifecycle`/`connection-registry` exports.
- `windows-backdrop.test.ts` — needs `translucency.applyWindowsBackdrop`.
- `local-gateway-python-{bundled,launcher-argv}.test.ts`, `local-gateway-python.test.ts`,
  `desktop-redial-route-request.test.ts`, `oauth-login-flights.test.ts`,
  `remote-lifecycle-{gate-transport,profile-missing,v2-marker}.test.ts`,
  `update-handoff-ack.test.ts`, `update-marker-{async-probe,corpus,readonly}.test.ts`,
  `updater/marker-helper.test.ts`, `updater/relaunch-waiter-spawn.windows-live.test.ts`,
  `window-growth.test.ts` — upstream tests whose providers or `main.ts` seams
  are still behind here.

**Renderer `src/`: 844 files** (167 `app`, 101 `i18n`, 84 `lib`, 30 `store`,
25 `components`, 10 `plugins`, …) — outside an electron-main port; this is the
next desktop chunk and the reason `vite build` cannot be green yet.

**Other:** 19 `e2e/`, 12 `scripts/`, 2 `docs/` (`canonical-gateway-protocol.md`,
`canonical-groups.md`), `vitest.hermes-python.ts`, `public/hermes.png` (asset,
needs a Qubit-branded replacement rather than a copy), `ENGINEERING.md`.
