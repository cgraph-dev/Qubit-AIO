/**
 * Shared presentational atoms for `/qubit-workbench`.
 *
 * The page's honesty contract has exactly one vocabulary: every data region is
 * in one of `loading` / `not_available` / `error` / `ok`, and the two failure
 * branches must look different. `backend-not-available` is a *statement about
 * the deployment* ("this route is not mounted"), not a transient failure, so
 * it never offers a retry button that could not possibly help.
 */

import { AlertTriangle, PlugZap, RefreshCw } from "lucide-react";
import { type ReactNode } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import type { ApiResult } from "@/lib/workbench-api";

import type { PanelCopy } from "./panel-copy";

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

/** Transient backend failure — retry is meaningful here. */
export function PanelError({
  copy,
  message,
  status,
  onRetry,
}: {
  copy: PanelCopy;
  message: string;
  status: number | null;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="workbench-panel-error"
      className="flex flex-col gap-2 border border-destructive/40 bg-destructive/[0.06] px-4 py-4"
    >
      <span className="flex items-center gap-2 text-xs font-semibold text-destructive">
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
        {copy.region} failed to load
        {status !== null ? ` (HTTP ${status})` : ""}
      </span>
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
      className="flex items-center gap-2 px-4 py-6 text-xs text-text-secondary"
    >
      <Spinner className="shrink-0" />
      {label}
    </div>
  );
}

/**
 * Render the common loading / not-available / error branches of an
 * `ApiResult`, and hand the `ok` payload to `children`. Every panel goes
 * through this so no panel can quietly skip a failure branch.
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
        onRetry={onRetry}
      />
    );
  }
  return <>{children(result.data)}</>;
}

/** Small section heading used by all three panels. */
export function PanelHeading({
  title,
  actions,
}: {
  title: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex h-9 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
      <h2 className="font-mondwest normal-case truncate text-xs font-semibold tracking-wide text-text-secondary">
        {title}
      </h2>
      {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
    </div>
  );
}
