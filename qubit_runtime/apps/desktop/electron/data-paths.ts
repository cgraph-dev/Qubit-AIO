// data-paths.ts — typed re-export of the shared pure resolver in data-paths.mjs.
// The app imports these names here (extensionless, for the tsc/esbuild build);
// the CI smoke driver imports the .mjs directly because Node's type-stripping
// cannot resolve extensionless TypeScript imports.
//
// Pure re-exports only: no wrapper functions, no `const` aliases. Vite resolves
// extensionless `./data-paths` to data-paths.mjs (not this file), and its SSR
// runner binds a default-imported module AFTER the module body runs — so
// anything this file COMPUTES from the import is undefined under vitest while
// still being fine in the esbuild bundle. Everything therefore lives in the
// .mjs; this file only names it.
import {
  desktopOwnHomePlan,
  desktopOwnHomeRoot,
  platformDefaultHermesHome,
  platformDefaultQubitHome,
  prepareOwnHomeRoot,
  resolveDesktopHermesHome,
  resolveDesktopQubitHome,
  resolveDesktopUserData,
  sanitizeRepoPath,
  sharedHomeName
} from './data-paths.mjs'

export {
  desktopOwnHomePlan,
  desktopOwnHomeRoot,
  platformDefaultHermesHome,
  platformDefaultQubitHome,
  prepareOwnHomeRoot,
  resolveDesktopHermesHome,
  resolveDesktopQubitHome,
  resolveDesktopUserData,
  sanitizeRepoPath,
  sharedHomeName
}
export type { DesktopOwnHomePlan, HermesHomeOptions } from './data-paths.mjs'
