/* Owner-triggered Business OS setup.
 *
 * The Command Center and the operator timeline read the durable Business OS.
 * On a fresh profile that store has no tenant yet, so every read answers
 * `BUSINESS_NOT_FOUND: no Business OS tenant exists yet`. That is an honest
 * empty state, not a broken panel — but it is not usable until the owner
 * creates the local Personal scope.
 *
 * WHY THE BUTTON EXISTS
 * ---------------------
 * Creating a tenant is a write. The owner rule is that data is never created
 * silently by a panel that was merely opened, so this surface renders an
 * explicit setup state and calls the existing owner-gated bootstrap route
 * (`POST /api/office/business/bootstrap`) only when the operator presses the
 * button. A successful bootstrap tells the host to retry its read; the panel
 * then renders the real projection.
 *
 * This is a HOOK plus a pure renderer, not a component: the panels embed the
 * returned elements directly, and the office test harness (which records the
 * element tree without running nested function components) can assert the
 * setup UI, its button and its typed failure.
 *
 * React comes from the HOST: `R.createElement`, never an import.
 */
import type { OfficeGameShellProps } from "../OfficeGameShell";
import { BusinessApiError, bootstrapOfficeSeats } from "./business-api";

export interface BusinessSetupOptions {
  base?: string;
  fetchImpl?: typeof fetch;
  /** The surface the operator was trying to use ("command center", ...). */
  surface: string;
  /** Called after a successful bootstrap so the host can retry its read. */
  onBootstrapped?: () => void;
}

export type BusinessSetupState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done" }
  | { kind: "error"; code: string; detail: string };

export interface BusinessSetupHandle {
  state: BusinessSetupState;
  start: () => void;
  render: () => unknown;
}

/** Whether a read failure means "this profile has no Business OS yet". */
export function isTenantMissing(code: string | undefined | null): boolean {
  return String(code ?? "") === "BUSINESS_NOT_FOUND";
}

/** The pure surface: same markup for either panel, no hidden state. */
export function renderBusinessSetup(
  R: OfficeGameShellProps["React"],
  state: BusinessSetupState,
  surface: string,
  onStart: () => void,
  onRetry: () => void,
): unknown {
  const running = state.kind === "running";
  const children: unknown[] = [
    R.createElement("p", {
      key: "eyebrow",
      className: "business-setup__eyebrow",
    }, "First run · setup required"),
    R.createElement("h3", {
      key: "title",
      className: "business-setup__title",
    }, "The Business OS is not set up yet"),
    R.createElement("p", {
      key: "lead",
      className: "business-setup__lead",
    }, `The ${surface} reads Qubit's durable Business OS, and this `
      + "profile has no tenant yet. Set it up once to create the local "
      + "Personal workspace and import the office seat roster."),
    R.createElement("p", {
      key: "note",
      className: "business-setup__note",
    }, "Nothing is created until you press the button. Setup is local, "
      + "idempotent, and never spends, contacts anyone, or calls a model."),
  ];

  if (state.kind === "done") {
    children.push(R.createElement("p", {
      key: "done",
      className: "business-setup__ok",
      "data-testid": "business-setup-done",
      role: "status",
      "aria-live": "polite",
    }, "Business OS is ready. Reloading this panel…"));
    // If the retried read still cannot see the tenant (a slow/foreign store),
    // the operator gets an explicit re-read rather than a dead end.
    children.push(R.createElement("button", {
      key: "retry",
      type: "button",
      className: "business-setup__start",
      "data-testid": "business-setup-retry",
      "aria-label": `Reload the ${surface} after setup`,
      onClick: onRetry,
    }, "Reload this panel"));
  } else {
    children.push(R.createElement("button", {
      key: "start",
      type: "button",
      className: "business-setup__start",
      "data-testid": "business-setup-start",
      "aria-label": "Create the Qubit Business OS workspace",
      disabled: running,
      onClick: onStart,
    }, running ? "Setting up the Business OS…" : "Set up Business OS"));
  }

  if (state.kind === "error") {
    children.push(R.createElement("p", {
      key: "error",
      className: "business-setup__error",
      "data-testid": "business-setup-error",
      "data-code": state.code,
      role: "alert",
    }, [
      R.createElement("strong", { key: "label" }, "Setup could not run. "),
      R.createElement("span", { key: "detail" },
        `${state.code}: ${state.detail}`),
      R.createElement("span", { key: "hint", className: "business-setup__hint" },
        " Check that this dashboard is running with the owner session and "
        + "that the Business OS bridge is enabled, then try again."),
    ]));
  }

  return R.createElement("section", {
    className: "business-setup",
    "data-testid": "business-setup",
    "data-state": state.kind,
    role: "region",
    "aria-label": `Set up the Business OS for the ${surface}`,
  }, children);
}

/**
 * Create the setup hook for one panel factory.
 *
 * The hook is called unconditionally at the top of the panel component, so the
 * hook order stays stable across renders. `start` performs the only write this
 * surface can cause; it never runs on mount.
 */
export function createBusinessSetup(
  R: OfficeGameShellProps["React"],
  hooks: OfficeGameShellProps["hooks"],
) {
  return function useBusinessSetup(
    opts: BusinessSetupOptions,
  ): BusinessSetupHandle {
    const [state, setState] = hooks.useState<BusinessSetupState>({ kind: "idle" });

    const start = (): void => {
      if (state.kind === "running") return;
      setState({ kind: "running" });
      bootstrapOfficeSeats({}, {
        base: opts.base,
        fetchImpl: opts.fetchImpl,
      }).then(() => {
        setState({ kind: "done" });
        opts.onBootstrapped?.();
      }).catch((exc: unknown) => {
        const code = exc instanceof BusinessApiError
          ? exc.code
          : "BUSINESS_SETUP_FAILED";
        const detail = exc instanceof Error ? exc.message : String(exc);
        setState({ kind: "error", code, detail });
      });
    };

    return {
      state,
      start,
      render: () => renderBusinessSetup(
        R, state, opts.surface, start, () => opts.onBootstrapped?.()),
    };
  };
}
