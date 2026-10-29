// data-paths.ts — typed re-export of the shared pure resolver in data-paths.mjs.
// The app imports these names here (extensionless, for the tsc/esbuild build);
// the CI smoke driver imports the .mjs directly because Node's type-stripping
// cannot resolve extensionless TypeScript imports.
import {
  platformDefaultHermesHome,
  resolveDesktopHermesHome,
  resolveDesktopUserData
} from './data-paths.mjs'

export { platformDefaultHermesHome, resolveDesktopHermesHome, resolveDesktopUserData }
export type { HermesHomeOptions } from './data-paths.mjs'

// Canonical aliases. Explicit bindings (not `export { x as y }`) because the
// bundler dropped the aliased re-export names.
export const platformDefaultQubitHome = platformDefaultHermesHome
export const resolveDesktopQubitHome = resolveDesktopHermesHome
