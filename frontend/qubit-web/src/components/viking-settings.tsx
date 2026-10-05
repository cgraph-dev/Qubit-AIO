/**
 * Open Viking settings-surface helpers.
 *
 * Thin markup helpers that emit the shared settings contract
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`)
 * used across the console. These helpers exist so every Open Viking page
 * composes the same DOM hierarchy from one place instead of hand-rolling it.
 *
 * Contract rule (see docs/DEEP_PARITY_FINDINGS.md): `.settings-row__control`
 * is emitted ONLY when a control is supplied — a row with no control must not
 * render an empty control wrapper.
 *
 * All base class names below are shipped by
 * `styles/upstream/upstream-sessions.css` and `styles/upstream/upstream-chrome.css`;
 * the shell-only classes (`.viking-intro`, `.viking-breadcrumb`,
 * `.viking-subnav`) are shipped by `styles/upstream/viking-settings.css`.
 * None are invented here.
 */

import type { ReactNode } from "react";
import { Link } from "react-router";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/** The five Open Viking console routes, in nav order. Shared so every page
 *  renders the same sibling switcher. */
export const VIKING_CONSOLE_VIEWS: ReadonlyArray<{ path: string; label: string }> = [
  { path: "/open-viking", label: "Playground" },
  { path: "/open-viking/retrieval", label: "Retrieval" },
  { path: "/open-viking/uploads", label: "Uploads" },
  { path: "/open-viking/agent-hub", label: "Agent Hub" },
  { path: "/open-viking/resources", label: "Resources" },
];

export interface SettingsPageIntroProps {
  /** One-line orientation sentence; identical treatment on all five pages. */
  children: ReactNode;
  /** Current route path — when supplied (and not the root) a parent
   *  breadcrumb renders so sub-pages share the same "where am I" line. */
  currentPath?: string;
  /** Root of the console family; the breadcrumb links here. */
  rootPath?: string;
  rootLabel?: string;
  /** Optional sibling-view switcher rendered under the intro. */
  showSubnav?: boolean;
  /** Extra class on the wrapper. */
  className?: string;
}

/**
 * Uniform page opening for the five Open Viking routes: the intro line, an
 * optional parent breadcrumb, and an optional sibling-view sub-nav. Every
 * page uses this so no page invents its own header pattern (the layout owns
 * the `h1` via ROUTE_TITLES, so no heading is emitted here).
 */
export function SettingsPageIntro({
  children,
  currentPath,
  rootPath = "/open-viking",
  rootLabel = "Open Viking",
  showSubnav,
  className,
}: SettingsPageIntroProps) {
  const isRoot = !currentPath || currentPath === rootPath;
  return (
    <header className={cn("viking-intro", className)}>
      {!isRoot && currentPath && (
        <p className="viking-breadcrumb" data-testid="viking-breadcrumb">
          <Link to={rootPath} className="viking-breadcrumb__link">
            {rootLabel}
          </Link>
          <ChevronRight className="viking-breadcrumb__sep h-3.5 w-3.5" aria-hidden />
          <span>{VIKING_CONSOLE_VIEWS.find((v) => v.path === currentPath)?.label ?? "Console"}</span>
        </p>
      )}
      <p className="settings-page__intro">{children}</p>
      {showSubnav && (
        <nav className="viking-subnav" aria-label="Open Viking views" data-testid="viking-subnav">
          {VIKING_CONSOLE_VIEWS.map((view) => (
            <Link
              key={view.path}
              to={view.path}
              className="viking-subnav__item"
              aria-current={currentPath === view.path ? "page" : undefined}
            >
              {view.label}
            </Link>
          ))}
        </nav>
      )}
    </header>
  );
}

/** A row that renders a control honestly disabled WITH ITS REASON visible.
 *  Used wherever a bridge row has not landed: the reason is always on the
 *  page, never hidden in a tooltip. */
export function SettingsDisabledRow({
  title,
  reason,
  testId,
  action,
}: {
  title: ReactNode;
  reason: string;
  testId?: string;
  /** Optional disabled control; it is rendered `disabled` with `title` reason. */
  action?: ReactNode;
}) {
  return (
    <SettingsRow
      title={title}
      description={reason}
      testId={testId}
      control={
        action ? (
          <span title={reason} className="inline-flex">
            {action}
          </span>
        ) : undefined
      }
    />
  );
}

export interface SettingsSectionProps {
  title?: string;
  description?: string;
  /** Optional count badge rendered inside the heading. */
  count?: number;
  /** Optional right-aligned header actions; the actions div is only emitted when supplied. */
  actions?: ReactNode;
  /** Rows for the section's settings group. */
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
}

/** `<section class="settings-section">` with the header/group hierarchy. */
export function SettingsSection({
  title,
  description,
  count,
  actions,
  children,
  className,
  ariaLabel,
}: SettingsSectionProps) {
  return (
    <section className={cn("settings-section", className)} aria-label={ariaLabel}>
      {(title || description || actions) && (
        <div className="settings-section__header">
          {(title || description) && (
            <div className="settings-section__copy">
              {title && (
                <h2 className="settings-section__heading">
                  {title}
                  {typeof count === "number" && (
                    <span className="settings-count"> {count}</span>
                  )}
                </h2>
              )}
              {description && (
                <p className="settings-section__desc">{description}</p>
              )}
            </div>
          )}
          {actions && <div className="settings-section__actions">{actions}</div>}
        </div>
      )}
      <div className="settings-group">{children}</div>
    </section>
  );
}

export interface SettingsRowProps {
  title?: ReactNode;
  description?: ReactNode;
  /** Emitted only when supplied — never an empty wrapper. */
  control?: ReactNode;
  stacked?: boolean;
  className?: string;
  as?: "div" | "button";
  onClick?: () => void;
  testId?: string;
  disabled?: boolean;
  /** Reason shown via the row description when a control is honestly refused. */
  ariaLabel?: string;
}

/** `<div class="settings-row">` with optional text and an optional control. */
export function SettingsRow({
  title,
  description,
  control,
  stacked,
  className,
  as = "div",
  onClick,
  testId,
  disabled,
  ariaLabel,
}: SettingsRowProps) {
  const body = (
    <>
      {(title || description) && (
        <div className="settings-row__text">
          {title && <span className="settings-row__title">{title}</span>}
          {description && <span className="settings-row__desc">{description}</span>}
        </div>
      )}
      {control && <div className="settings-row__control">{control}</div>}
    </>
  );

  if (as === "button") {
    return (
      <button
        type="button"
        className={cn("settings-row settings-row--nav", stacked && "settings-row--stacked", className)}
        onClick={onClick}
        data-testid={testId}
        disabled={disabled}
        aria-label={ariaLabel}
      >
        {body}
        <span className="settings-row__chevron" aria-hidden>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="m9 18 6-6-6-6" />
          </svg>
        </span>
      </button>
    );
  }

  return (
    <div
      className={cn("settings-row", stacked && "settings-row--stacked", className)}
      data-testid={testId}
      aria-label={ariaLabel}
    >
      {body}
    </div>
  );
}

/** `<p class="settings-empty">` empty-state line. */
export function SettingsEmpty({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className="settings-empty" data-testid={testId}>
      {children}
    </p>
  );
}

/** `<div class="settings-loading-skeleton">` loading placeholder rows. */
export function SettingsLoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="settings-loading-skeleton" aria-hidden>
      <div className="settings-loading-skeleton__rows">
        {Array.from({ length: rows }).map((_, i) => (
          <div className="settings-row settings-loading-skeleton__row" key={i}>
            <div className="settings-row__text">
              <span className="settings-loading-skeleton__title" />
              <span className="settings-loading-skeleton__description" />
            </div>
            <span className="settings-loading-skeleton__control" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** `<span class="settings-status">` dot + text status marker. */
export function SettingsStatus({
  tone = "ok",
  children,
}: {
  tone?: "ok" | "warn" | "danger" | "accent";
  children: ReactNode;
}) {
  return (
    <span className={cn("settings-status", tone !== "ok" && `settings-status--${tone}`)}>
      <span className="settings-status__dot" aria-hidden />
      <span>{children}</span>
    </span>
  );
}
