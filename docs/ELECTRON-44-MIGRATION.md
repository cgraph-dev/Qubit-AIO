# Electron 44 migration plan — Qubit Desktop

**Status:** plan only. No upgrade was performed in this session; `package.json`,
the lockfile, `node_modules`, and the packaged build are untouched. Read the
blockers before starting: stock Electron 44 does **not** provide the Widevine
`components` API this plan needs.

## Why

`electron/player/runtime.ts` probes `components.WIDEVINE_CDM_ID`,
`components.whenReady([id])`, and `components.status()`. The shipped Electron
40.10.2 has no `components` module, so the probe returns the typed
`unsupported` state and protected playback stays off. Upstream's host pins
Electron 44.9.3, and QubitTV's DRM path needs that component API.

## Measured baseline

| Fact | Current | Target |
|---|---|---|
| Electron dependency | `40.10.2` (`package.json:186`) | `44.9.3+wvcus` (castlabs ECS) |
| Bundle | Chromium 144.0.7559.236, Node 24.15.0, V8 14.4 | Chromium 152.0.7977.167, Node 24.22.0, V8 15.2 |
| Native module ABI | 143 | 149 (`node-abi.getAbi('44.9.3','electron')`) |
| Host build Node | 22.22.3 (engines `^22.22 || ^24.11 || >=26`) | unchanged (castlabs package needs `node >=22.12`) |
| `@electron/rebuild` | 4.2.0 | unchanged if its hoisted `node-abi` resolves 149 |
| `node-pty` | 1.1.0; Linux has **no** `prebuilds/linux-x64` | must be rebuilt for ABI 149 on Linux |
| `electron-builder` | 27.0.0-alpha.6 | verify 44 artifact/ASI/SignTool behaviour |
| macOS runtime floor | macOS 12 possible today | **macOS 13+** (Electron 44) |

## Hard blockers

### 1. Stock Electron 44 does not ship Widevine
Electron 44 upstream has no `components` API. The required runtime is
**castlabs Electron for Content Security** `44.9.3+wvcus`, installed from
GitHub:
`npm install "https://github.com/castlabs/electron-releases#v44.9.3+wvcus" --save-dev`.
Its package version is `44.9.3+wvcus`; `electron.d.ts` supplies the
`components` typings, so `runtime.ts` can drop its local `RuntimeComponents`
cast and use the real module. Do not claim DRM if the binary is stock.

### 2. Packaging downloads stock Electron, not the fork
`scripts/prepare-packaging-tools.mjs:89` calls electron-builder's
`downloadElectronArtifactZip({ version: config.electronVersion, ... })`, which
always fetches the upstream Electron release. `scripts/run-electron-builder.mjs:148`
then passes that zip as `-c.electronDist`. Packaging a castlabs `node_modules`
without changing acquisition silently ships a stock runtime with no CDM.

Required change: prepare the **castlabs dist** (or a zip of it) and keep
`electronVersion` numeric-only for electron-builder/`@electron/rebuild`
headers. `electronDist` must point at the fork's dist. Verify ASAR integrity,
`rcedit`, MSIX identity/signing, and macOS hardened runtime against the fork.

### 3. The version guard rejects the fork
`electron-builder.config.cjs:62-64` requires `^\d+\.\d+\.\d+$`, and derives
`electronVersion` straight from the dependency (`:72`). A git spec
(`…/electron-releases#v44.9.3+wvcus`) fails both. Keep a separate numeric
`44.9.3` constant (from the tag) for builder/header/ABI use and let the
dependency spec stay the castlabs URL.

`electron/desktop-electron-pin.test.ts` currently asserts an exact semver
dependency whose version equals the lockfile entry. It must be updated to
accept the castlabs git spec and to compare the **tag version** (`44.9.3`)
plus the resolved lock commit — otherwise the pin test fails or, worse, passes
against the wrong binary.

### 4. Native ABI 143 → 149
- Linux `node-pty` is built from source (`stage-native-deps.mjs:304-410`), so
  it will be rebuilt against ABI 149 by `@electron/rebuild`. Confirm the
  hoisted `node-abi` knows 44 (this host's `node-abi` resolves 149 today).
- macOS/Windows `node-pty` prebuilds are shipped for `darwin-*`/`win32-*`; if
  they are not Node-API they must be re-checked against ABI 149. The staging
  script validates platform, not ABI.
- `get-windows` (optional) has its own installer path
  (`stage-native-deps.mjs:551-627`); re-run it and verify the binding.
- castlabs 44.9.3 depends on `@electron-internal/extract-zip` (native N-API);
  the same class of Windows `ERR_DLOPEN_FAILED` the 40.10.3 incident hit
  (`electron/desktop-electron-pin.test.ts` docstring). This affects installing
  the dependency, not the app ABI; verify on a clean Windows host.

### 5. Electron 44 API changes that hit this code
From the Electron breaking-changes list (41, 42, 43, 44):

- **`clipboard` is async in main and removed from renderers.** Main uses
  `clipboard.writeText` (`electron/main.ts:16753`), `clipboard.readText`
  (`:16778`), and `clipboard.readImage` (`:16865`). `readImage` no longer
  exists; the read/write helpers return Promises and accept MIME/`ClipboardItem`
  shapes. These call sites must move to `await clipboard.*` plus
  `ClipboardItem`/`read()` or a preload helper. Renderer code already uses
  `navigator.clipboard`, so no renderer import is expected to break, but the
  IPC handlers' signatures do.
- **Screen/display capture permission.** Electron 44.3 reports capture
  requests as `display-capture` (previously `media` with empty types). The
  default handlers at `electron/main.ts:6938/6945` and the player handler at
  `electron/player/manager.ts:1126` must allow `display-capture` explicitly for
  screen sharing/`desktopCapturer` paths.
- **macOS 12 removed.** Raise the minimum to macOS 13 and verify the packaged
  `LSMinimumSystemVersion`. `openAsHidden`/`wasOpenedAsHidden`/`restoreState`
  are gone (none are used in this tree).
- **macOS notifications require code signing** (Electron 42 UNNotification).
  Unsigned dev builds will emit `failed`; notarized builds must be used.
- **32-bit targets removed.** `scripts/run-electron-builder.mjs:15` still
  accepts `--ia32`/`--armv7l`; reject them or remove them before any release
  matrix so they fail early with a clear message.
- **ANGLE is static** (no `libEGL`/`libGLESv2` files). Any packaging/GPU
  allowlist that expects those files or GPU-process-only loading must be
  re-checked.
- **`window.open` children from unsandboxed windows get their own sandboxed
  process** (opener null). Verify pop-out/hub windows that rely on
  `window.opener`.
- **PDFs are OOPIFs**, dialogs default to Downloads, `net.request` frame
  destinations need `Sec-Fetch-Mode: navigate`, OSR defaults to scale factor
  1.0, and `showHiddenFiles` is gone on Linux. Check the link-title OSR window,
  file dialogs, OAuth `net.request`, and preview PDFs accordingly.

### 6. Legal / distribution
ECS itself is MIT, but the Widevine CDM is proprietary and downloaded by the
component updater at runtime. Production playback requires castlabs EVS/VMP
signing for services that enforce VMP; Linux has no persistent licenses and
first install requires an app restart. Record the castlabs pin, license, and
Widevine terms in `THIRD_PARTY_NOTICES.md` before shipping.

### 7. CI
No workflow in this tree builds or packages the desktop app. The self-hosted
Linux runner only compiles Python. A desktop release job must exist before an
upgrade can be verified there; it needs Node >=22.12, a C++ toolchain for
`node-pty`, the castlabs GitHub dependency, and the signing credentials.

## Upgrade steps (when approved)

1. Branch from the current pin (`fix/desktop-types` or a new
   `feat/electron-44`), in its own worktree.
2. Add the castlabs dependency at the exact release:
   `npm install "https://github.com/castlabs/electron-releases#v44.9.3+wvcus" --save-dev`
   (this **is** the one time `npm install` is expected; commit the lockfile).
3. Add a numeric `ELECTRON_VERSION = '44.9.3'` source of truth; point
   `electron-builder.config.cjs` at it and relax the regex to strip build
   metadata. Keep the castlabs spec in `package.json` and update
   `electron/desktop-electron-pin.test.ts`.
4. Change packaging preparation to ship the castlabs dist (directory or zip)
   into `electronDist`; never let `downloadElectronArtifactZip` fetch stock
   44.9.3. Add a pre-package assertion that the binary reports `+wvcus` and
   exports `components`.
5. Update `electron/player/runtime.ts` to import `components` from Electron
   directly, remove the cast, and pin the real status vocabulary (verify
   `updated`/`ready`/`installed` on Linux and Windows).
6. Convert main-process clipboard call sites to the async API, including the
   screenshot-to-clipboard path; keep the renderer on `navigator.clipboard`.
7. Add `display-capture` to the default and player permission handlers.
8. Reject `--ia32`/`--armv7l`, raise macOS minimum to 13, and re-check
   notifications signing, ANGLE assumptions, OSR, dialogs, `net.request`, and
   `window.open` children.
9. Rebuild native deps (`node scripts/stage-native-deps.mjs`), then package
   (`npm run dist:*`) on each target host.
10. Run the focused desktop matrix (Electron spawn/lifecycle, QubitTV 20-file
    suite), `tsc -p .`/`tsc -p tsconfig.electron.json`, `vite build`, and a
    live packaged playback test: YouTube (non-DRM) plus one Widevine service,
    with the readiness probe showing `ready` (Linux may show
    `restart-required` on first install, then `ready` after restart).

## Verification checklist

- `electron/player/runtime.test.ts`: real `components` object drives
  ready/restart/unsupported.
- Packaged binary: `process.versions.electron === '44.9.3'`, component API
  present, `components.status()` includes `WIDEVINE_CDM_ID`.
- Widevine: first `components.whenReady([WIDEVINE_CDM_ID])` either resolves
  ready or returns the documented restart-required state on Linux; a second
  launch resolves ready.
- Clipboard: copy/paste text and screenshot-to-clipboard both work from the
  packaged app.
- `npx --no-install vite build` exits 0; QubitTV focused suite green;
  spawn/lifecycle suite green.

## Rollback

1. `git revert` the upgrade commit(s) and restore `package.json` +
   `package-lock.json` to Electron `40.10.2`.
2. Remove the castlabs `electronDist` preparation and restore the stock
   `downloadElectronArtifactZip` path.
3. Delete `node_modules/electron` and reinstall (or restore the previous
   lockfile), then rebuild native deps for ABI 143.
4. Repackage 40.10.2 and confirm `runtime.ts` again returns the typed
   `unsupported` state (no DRM claim, no crash).
5. Keep the branch; do not push or ship until the checklist passes.
