# DESKTOP appId — what a rename actually costs

**Status:** assessment only. No code changed. This is an **owner sign-off item**,
because the answer depends on a distribution decision, not on engineering.

**Measured at:** `78d73efe9` + `418ba9272`, tree clean.

---

## 1. The premise needs correcting first

The change request assumed:

> changing appId relocates userData, so every existing user loses their window
> state and settings

**That is false for this codebase**, and the reason is load-bearing. Verified
in `electron/product-identity.ts:38-47` and `electron/main.ts:578,1002`:

```js
// product-identity.ts:38 — early return for the stable build
if (!identity.token && identity.appNamePascal === identity.artifactNamePascal) {
  return null            // ← stable (shipping) build always takes this
}
// only nonstable paths reach here:
const userData = path.join(app.getPath('appData'), identity.appNamePascal)
app.setPath('userData', userData)
```

For the shipping identity (`applyDesktopIdentity` returns `null`, confirmed by
running it against `product-identity.cjs`: `token: undefined`,
`appNamePascal === artifactNamePascal === "Qubit"`), the override never runs.
userData therefore stays **Electron's default**, which is
`appData/<app.getName()>`, and `app.getName()` resolves
`productName ?? name` — i.e. `"Qubit"` (`extraMetadata.name = appNamePascal`).

Observed on this machine: `~/.config/Qubit`, created `2026-11-08`, matching
`~/.config/Qubit` exactly. **`appId` is not an input to userData anywhere in
this tree.** Renaming it does not move anyone's settings or window state.

The `task`'s second concern (silently moving user data) is therefore not
triggered — but that does **not** make the rename free. §2 is the real cost.

## 2. What `appId` actually drives

`appId` comes from `product-identity.cjs:78` — `com.nousresearch.${kebab}${suffix}`
— and reaches these consumers:

| Consumer | Mechanism | Breaks on rename? |
|---|---|---|
| **Linux `.desktop` filename** | `extraMetadata.desktopName = appId` (`electron-builder.config.cjs:114`) → `getDesktopFileName()` | Yes — file becomes `<new-id>.desktop`. Verified in the built deb: `./usr/share/applications/com.nousresearch.qubit.desktop`. |
| **Linux `StartupWMClass`** | same `desktopName`, via `computeDesktopEntry` | Yes — window/dock association match is by this exact string |
| **Windows AUMID** | `app.setAppUserModelId(PRODUCT_IDENTITY.appId)` (`electron/main.ts:1412`) | Yes — must equal the installed Start-menu shortcut's AUMID or toast notifications stop firing |
| **macOS `CFBundleIdentifier`** | `appInfo.macBundleIdentifier` (`app-builder-lib/dist/appInfo.js:107`) | Yes — and a *changed* bundle id is a **different app** to macOS: LaunchServices treats it as new, so the old install's launch services registrations, TCC privacy grants (mic, screen recording, accessibility) and login-item entry are all re-prompted or orphaned |
| **Flatpak app id** | `filterFlatpakAppIdentifier(appInfo.id)` | Only if Flatpak is ever targeted (currently not) |
| **deb/rpm package name** | *not* appId — uses `metadata.name` | No. Verified: `dpkg-deb -f … Package` → `qubit` |

## 3. Why this must wait for owner sign-off

Three reasons, in order of weight:

1. **It is a distribution-identity decision, not a code cleanup.** The
   `com.nousresearch.*` prefix is the vendor namespace. `docs/BRAND.md` §1 scopes
   the zero-legacy-brand rule to "a surface they can read or operate" and
   explicitly says "the rule is about **what ships to a user**, not about
   identifiers. Internal names are not user-facing just because they contain the
   old word." A bundle id in `wmctrl -x` output is not something a user reads.
   Whether the vendor *namespace* must change is a legal/product call about who
   owns the app on each store — not something an agent should decide unilaterally.

2. **macOS TCC regression risk is real and untestable here.** Changing
   `CFBundleIdentifier` resets privacy grants. This box is Linux; the change
   cannot be exercised for the one platform where it has the worst failure mode.
   Shipping an unverified TCC regression to macOS is not acceptable under the
   placeholder rules in `AGENTS.md`.

3. **A partial rename is worse than none.** Per `docs/BRAND.md` §4.1, writer and
   reader move together. `appId` has four independent writers (desktop filename,
   WMClass, AUMID, bundle id) and one reader (`main.ts:1412`). Renaming the
   appId without simultaneously re-pointing the packaged MSIX
   `applicationId`/AUMID pairing and re-registering Launch Services would leave
   Windows notifications silently dead — the exact failure `main.ts:1405-1412`
   documents having already been fixed once.

## 4. What was done instead

The one appId-derived token that **is** on a user-facing surface is the `.desktop`
filename and `StartupWMClass`. Both stay `com.nousresearch.qubit` until §3 is
ruled on. Nothing was renamed.

The **MIME scheme** separately was fixed — not by repointing it, but by
removing the *drift*: the scheme is now a single source
(`product-identity.cjs` `deepLinkScheme`) read by both the packager and the
runtime, replacing two hardcoded literals that only agreed by luck. See
`418ba9272`.

## 5. The decision required

Pick one:

- **(A) Keep `com.nousresearch.qubit`.** Zero user impact. Vendor namespace
  remains in bundle id / desktop filename for every user who runs
  `ls ~/.local/share/applications`. Defensible under `BRAND.md` §1 as an
  identifier, not a surface.
- **(B) Rename to a Qubit-owned namespace** (e.g. `com.cgraph.qubit`). Requires,
  in one release: new `.desktop` filename + WMClass, matching MSIX ApplicationId,
  macOS Launch Services re-registration, and an explicit note in release notes
  that macOS will re-prompt for microphone/screen-recording/accessibility.
  Requires a macOS test machine and a Windows AUMID verification.

**(B) is the only option that changes bytes on user machines, and it cannot be
verified from this platform. It is an owner call.**
