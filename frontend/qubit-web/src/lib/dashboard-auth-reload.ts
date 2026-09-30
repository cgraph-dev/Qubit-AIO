type StorageLike = Pick<Storage, "getItem" | "removeItem" | "setItem">;

const TOKEN_RELOAD_STORAGE_KEY = "qubit.tokenReloadAttempted";
/** Pre-rebrand key, still checked/cleared so a guard set before the rename
 *  does not linger. */
const LEGACY_TOKEN_RELOAD_STORAGE_KEY = "hermes.tokenReloadAttempted";

function dashboardAuthRequired(): boolean {
  return typeof window !== "undefined" && !!window.__QUBIT_AUTH_REQUIRED__;
}

function reloadDashboardWindow(): void {
  if (typeof window !== "undefined") {
    window.location.reload();
  }
}

function dashboardSessionStorage(): StorageLike | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function clearDashboardTokenReloadAttempt(
  storage: StorageLike | null = dashboardSessionStorage(),
): void {
  try {
    storage?.removeItem(TOKEN_RELOAD_STORAGE_KEY);
    storage?.removeItem(LEGACY_TOKEN_RELOAD_STORAGE_KEY);
  } catch {
    /* privacy mode / blocked storage — ignore */
  }
}

export function attemptDashboardTokenReloadOnce(
  storage: StorageLike | null = dashboardSessionStorage(),
  reload: () => void = reloadDashboardWindow,
): boolean {
  let alreadyReloaded = false;
  try {
    alreadyReloaded =
      storage?.getItem(TOKEN_RELOAD_STORAGE_KEY) === "1" ||
      storage?.getItem(LEGACY_TOKEN_RELOAD_STORAGE_KEY) === "1";
  } catch {
    /* privacy mode / blocked storage — fall through */
  }
  if (alreadyReloaded) {
    return false;
  }

  try {
    storage?.setItem(TOKEN_RELOAD_STORAGE_KEY, "1");
  } catch {
    /* privacy mode / blocked storage — best effort */
  }

  reload();
  return true;
}

export function maybeReloadForLoopbackWsAuthFailure(
  code: number,
  authRequired = dashboardAuthRequired(),
  storage: StorageLike | null = dashboardSessionStorage(),
  reload: () => void = reloadDashboardWindow,
): boolean {
  if (authRequired || code !== 4401) {
    return false;
  }
  return attemptDashboardTokenReloadOnce(storage, reload);
}
