declare global {
  interface Window {
    // Wire identifiers injected by the dashboard HTML (preserved as-is per the
    // load-bearing-identifier rule; cf. __QUBIT_SESSION_TOKEN__ in `@/lib/api`).
    __QUBIT_DASHBOARD_PROFILE__?: string;
    __QUBIT_INITIAL_PROFILE__?: string;
  }
}

/**
 * Ported from the pinned upstream `web/src/lib/profile-bootstrap.ts`
 * (`d337b736aa1e8ebecfab043842d13e4a2d2f48a3`); only the comment's home name is
 * de-branded (QUBIT_HOME).
 */

export function dashboardInitialProfile(): string {
  if (typeof window === "undefined") return "";
  return window.__QUBIT_INITIAL_PROFILE__ ?? "";
}

/**
 * The profile this backend process itself serves, injected by the server and
 * empty when it cannot be named unambiguously (custom QUBIT_HOME).
 *
 * It is the LAST fallback for the management scope: without it the dashboard
 * sends no `?profile=` at all, which a multi-profile host now refuses (400) on
 * every destructive route. It is never a guess — the backend only emits a name
 * that provably resolves back to its own home, so it targets exactly the home
 * an unnamed request used to reach.
 */
export function dashboardServingProfile(): string {
  if (typeof window === "undefined") return "";
  return window.__QUBIT_DASHBOARD_PROFILE__ ?? "";
}

export function initialProfileScope(
  searchParams: URLSearchParams,
  bootstrapProfile = dashboardInitialProfile(),
  servingProfile = dashboardServingProfile(),
): string {
  const urlProfile = searchParams.get("profile");
  if (urlProfile !== null) return urlProfile;
  return bootstrapProfile || servingProfile;
}

export function shouldAdoptActiveProfile(
  urlProfile: string | null,
  bootstrapProfile: string,
  currentProfile: string,
  activeProfile: string,
): boolean {
  return urlProfile === null && !bootstrapProfile && activeProfile !== currentProfile;
}
