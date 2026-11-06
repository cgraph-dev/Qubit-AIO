/**
 * Shared presentational atoms for `/qubit-workbench`.
 *
 * The page's honesty contract has exactly one vocabulary, and this module is
 * where it is expressed. A data region is in one of five states, and the last
 * three must look different from the first two:
 *
 * - `loading`        nothing read yet;
 * - `ok`             the backend answered with rows;
 * - `empty`          the backend answered, and its own code says why there is
 *                    nothing (`NO_ACTIVE_PROJECT`). The reason is quoted;
 * - `not_available`  the route is not mounted. This is a statement about the
 *                    DEPLOYMENT, so it offers no retry button that could not
 *                    help;
 * - `error`          everything else, including typed backend refusals, whose
 *                    `code` is quoted verbatim.
 *
 * `GapNotice` is the sibling used by the two workspace tabs that have no
 * backend at all: it names the route that would have to exist.
 */

import { AlertTriangle, PlugZap, RefreshCw } from "lucide-react";
import { type ReactNode } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import type { ApiResult } from "@/lib/workbench-api";

import type { PanelCopy } from "./panel-copy";
import { HEAD_ICON_BUTTON, PANEL_HEAD_LABEL, REGION } from "./workbench-theme";

/** The explicit "backend not available" panel. Never disguised as empty data. */
export function BackendUnavailable({
  copy,
  routes,
  compact = false,
}: {
  copy: PanelCopy;
  routes: readonly string[];
  compact?: boolean;
}) {
  return (
    <div
      role="status"
      data-testid="workbench-backend-not-available"
      className={
        compact
          ? "flex flex-col gap-1 border border-dashed border-warning/40 bg-warning/[0.04] px-3 py-2"
          : "flex flex-col gap-2 border border-dashed border-warning/40 bg-warning/[0.04] px-4 py-6"
      }
    >
      <span className="flex items-center gap-2 text-xs font-semibold text-warning">
        <PlugZap className="h-4 w-4 shrink-0" aria-hidden="true" />
        Backend not available
      </span>
      <p className="text-xs text-text-secondary">
        {copy.region}: {copy.purpose} The route behind this panel is not mounted
        yet, so nothing is shown rather than an empty result.
      </p>
      <ul className="flex flex-col gap-0.5 font-mono-ui text-[11px] text-text-tertiary">
        {routes.map((route) => (
          <li key={route}>{route}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A mounted backend that answered "nothing, for this named reason".
 *
 * Distinct from `BackendUnavailable` (the route exists and worked) and from a
 * plain empty list, which would claim the folder is empty.
 */
export function EmptyStateWithReason({
  title,
  detail,
  code,
  testId = "workbench-empty-with-reason",
}: {
  title: string;
  detail: string;
  code: string;
  testId?: string;
}) {
  return (
    <div
      role="status"
      data-testid={testId}
      className="flex flex-col items-start gap-1.5 px-3 py-6"
    >
      <p className="text-xs font-medium text-text-secondary">{title}</p>
      {detail ? (
        <p className="text-[11px] leading-relaxed text-text-tertiary">{detail}</p>
      ) : null}
      <p className="font-mono-ui text-[10px] text-text-tertiary/80">{code}</p>
    </div>
  );
}

/**
 * A capability this backend does not serve at all.
 *
 * Used by the Artifacts and Todos tabs. It names the missing route and the
 * reason it is missing; it never renders a fake count, a placeholder row, or a
 * "0 artifacts" that would read as "you have none".
 */
export function GapNotice({
  label,
  route,
  why,
  testId,
}: {
  label: string;
  route: string;
  why: string;
  testId: string;
}) {
  return (
    <div
      role="note"
      data-testid={testId}
      className="mx-2 my-2 flex flex-col gap-2 rounded-lg border border-dashed border-border px-3 py-3"
    >
      <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-text-secondary">
        <PlugZap className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {label} — not implemented on the Qubit side yet
      </span>
      <p className="text-[11px] leading-relaxed text-text-tertiary">{why}</p>
      <p
        className="font-mono-ui text-[10px] text-text-tertiary"
        title="The route this tab would read"
      >
        missing route: {route}
      </p>
    </div>
  );
}

/** Transient backend failure — retry is meaningful here. */
export function PanelError({
  copy,
  message,
  status,
  code,
  onRetry,
}: {
  copy: PanelCopy;
  message: string;
  status: number | null;
  /** The backend's typed refusal code, when it answered one. */
  code?: string | null;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="workbench-panel-error"
      className="m-2 flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/[0.06] px-3 py-3"
    >
      <span className="flex items-center gap-2 text-xs font-semibold text-destructive">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
        {copy.region} failed to load
        {status !== null ? ` (HTTP ${status})` : ""}
      </span>
      {code ? <p className="font-mono-ui text-[10px] text-destructive/90">{code}</p> : null}
      <p className="text-xs text-text-secondary">{message}</p>
      {onRetry ? (
        <span>
          <Button ghost size="sm" onClick={onRetry} prefix={<RefreshCw />}>
            Retry
          </Button>
        </span>
      ) : null}
    </div>
  );
}

export function PanelLoading({ label }: { label: string }) {
  return (
    <div
      role="status"
      data-testid="workbench-panel-loading"
      className="flex items-center gap-2 px-3 py-6 text-xs text-text-secondary"
    >
      <Spinner className="shrink-0" />
      {label}
    </div>
  );
}

/**
 * Render the common loading / not-available / empty / error branches of an
 * `ApiResult`, and hand the `ok` payload to `children`.
 *
 * `empty` is passed to `children` as well — the payload is still there (an
 * empty page, not an absent one) — so the panel decides which of "the reason"
 * or "no rows" applies.
 */
export function ResultGate<T>({
  result,
  copy,
  loadingText,
  onRetry,
  compact,
  children,
}: {
  result: ApiResult<T> | null;
  copy: PanelCopy;
  loadingText: string;
  onRetry?: () => void;
  compact?: boolean;
  children: (data: T) => ReactNode;
}) {
  if (result === null) return <PanelLoading label={loadingText} />;
  if (result.state === "not_available") {
    return <BackendUnavailable copy={copy} routes={result.routes} compact={compact} />;
  }
  if (result.state === "error") {
    return (
      <PanelError
        copy={copy}
        message={result.error.message}
        status={result.error.status}
        code={result.error.code}
        onRetry={onRetry}
      />
    );
  }
  return <>{children(result.data)}</>;
}

/** Small uppercase section heading used by all three panels. */
export function PanelHeading({
  title,
  actions,
  testId,
}: {
  title: string;
  actions?: ReactNode;
  testId?: string;
}) {
  return (
    <div className={REGION.panelHead} data-testid={testId}>
      <h2 className={PANEL_HEAD_LABEL} title={title}>
        {title}
      </h2>
      {actions ? <div className="flex shrink-0 items-center gap-0.5">{actions}</div> : null}
    </div>
  );
}

/** A panel-header icon button with a native tooltip. */
export function HeadIconButton({
  label,
  onClick,
  disabled,
  testId,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      data-testid={testId}
      className={HEAD_ICON_BUTTON}
    >
      {children}
    </button>
  );
}
