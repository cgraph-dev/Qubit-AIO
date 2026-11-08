export function platformDefaultHermesHome(
  home: string,
  env?: NodeJS.ProcessEnv,
  platform?: NodeJS.Platform,
): string

export function resolveDesktopUserData(defaultPath: string, env?: NodeJS.ProcessEnv): string

export interface HermesHomeOptions {
  home: string
  env?: NodeJS.ProcessEnv
  platform?: NodeJS.Platform
  directoryExists?: (directory: string) => boolean
  readWindowsHome?: () => string | null
}

export function resolveDesktopHermesHome(options: HermesHomeOptions): string

/** Canonical rebrand spelling of platformDefaultHermesHome. */
export function platformDefaultQubitHome(
  home: string,
  env?: NodeJS.ProcessEnv,
  platform?: NodeJS.Platform,
): string

/** Canonical rebrand spelling of resolveDesktopHermesHome. */
export function resolveDesktopQubitHome(options: HermesHomeOptions): string

export function sanitizeRepoPath(raw: unknown): string

export function desktopOwnHomeRoot(sharedHome: string): string

export function sharedHomeName(sharedHome: string): string

export type DesktopOwnHomeStatus = 'explicit' | 'first' | 'link' | 'own'

export interface DesktopOwnHomePlan {
  /** The rung this launch resolved (see desktopOwnHomePlan in data-paths.mjs). */
  status: DesktopOwnHomeStatus
  /** The home to spawn the backend with. */
  home: string
  /** Marker file proving this root belongs to the desktop. */
  configPath: string
  /** `<ownRoot>/profile`, the one link the desktop writes through. */
  linkPath: string
  ownHome: string
  ownRoot: string
  sharedHome: string
}

export function desktopHomeLayout(sharedHome: string): Omit<DesktopOwnHomePlan, 'home' | 'status'>

export function desktopOwnHomePlan(options: HermesHomeOptions): DesktopOwnHomePlan

/**
 * Create what the plan's rung needs and return the backend home, or null while
 * the own home is not usable yet (the caller keeps starting without a backend).
 */
export function prepareOwnHomeRoot(plan: DesktopOwnHomePlan): string | null
