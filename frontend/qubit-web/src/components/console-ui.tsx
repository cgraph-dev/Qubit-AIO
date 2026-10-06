/**
 * openclaw console primitives — React port of the upstream render helpers.
 *
 * Source: openclaw `ui/src/components/settings-ui.ts` (MIT), commit `7ff3b3be`.
 * Ported as structure + semantics, not verbatim code: the upstream is Lit
 * (`html` tagged templates) and this is React. Only the class vocabulary and the
 * CONDITIONAL-EMISSION rules are carried across, because those are what make the
 * rendered DOM 1:1.
 *
 * THE rule that matters (upstream `renderSettingsSection` / `renderSettingsRow`):
 *   - `.settings-section__copy`  → only when title or description exists
 *   - `.settings-section__header`→ only when copy or actions exist
 *   - `.settings-section__actions`→ only when actions exist
 *   - `.settings-row__control`   → ONLY when a control is passed
 *   - `.settings-count`          → only when count !== undefined
 * An always-rendered wrapper around nothing is the "extra empty panel" defect.
 */
import type { ReactNode } from "react";

/* ------------------------------------------------------------------ page */

export function SettingsPage({
  children,
  wide = false,
  className,
}: {
  children: ReactNode;
  wide?: boolean;
  /** Extra classes for the page root (e.g. the `qb-upstream` theme scope). */
  className?: string;
}) {
  return (
    <div
      className={`settings-page${wide ? " settings-page--wide" : ""}${
        className ? ` ${className}` : ""
      }`}
    >
      {children}
    </div>
  );
}

/** Upstream `renderSettingsPageHeader` — the h1 + optional description. */
export function SettingsPageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="settings-page__header">
      <div className="settings-page__copy">
        <h1 className="settings-page__title">{title}</h1>
        {description ? (
          <p className="settings-page__desc">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="settings-page__actions">{actions}</div> : null}
    </header>
  );
}

/* --------------------------------------------------------------- section */

export function SettingsSection({
  title,
  count,
  description,
  actions,
  notice,
  danger = false,
  ariaLabel,
  testId,
  children,
}: {
  title?: ReactNode;
  count?: number;
  description?: ReactNode;
  actions?: ReactNode;
  notice?: ReactNode;
  danger?: boolean;
  /** Passthrough handles so pages keep their `aria-label` / `data-testid`. */
  ariaLabel?: string;
  testId?: string;
  children: ReactNode;
}) {
  const copy =
    title || description ? (
      <div className="settings-section__copy">
        {title ? (
          <h2 className="settings-section__heading">
            {title}
            {count !== undefined ? (
              <span className="settings-count">{count}</span>
            ) : null}
          </h2>
        ) : null}
        {description ? (
          <p className="settings-section__desc">{description}</p>
        ) : null}
      </div>
    ) : null;

  // Header emits ONLY when there is something to put in it.
  const header =
    copy || actions ? (
      <div className="settings-section__header">
        {copy}
        {actions ? (
          <div className="settings-section__actions">{actions}</div>
        ) : null}
      </div>
    ) : null;

  return (
    <section
      className={`settings-section${danger ? " settings-section--danger" : ""}`}
      aria-label={ariaLabel}
      data-testid={testId}
    >
      {header}
      {notice ?? null}
      <SettingsGroup danger={danger}>{children}</SettingsGroup>
    </section>
  );
}

export function SettingsGroup({
  danger = false,
  children,
}: {
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`settings-group${danger ? " settings-group--danger" : ""}`}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------- row */

export function SettingsRow({
  title,
  description,
  control,
  stacked = false,
  stackedOnNarrow = false,
  role,
  testId,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Pass a node to get `.settings-row__control`; omit it to get no wrapper. */
  control?: ReactNode;
  stacked?: boolean;
  stackedOnNarrow?: boolean;
  role?: "alert" | "status";
  /** Passthrough so pages keep their `data-testid` handles on the row root. */
  testId?: string;
}) {
  const cls = [
    "settings-row",
    stacked ? "settings-row--stacked" : "",
    stackedOnNarrow ? "settings-row--stacked-on-narrow" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} role={role} data-testid={testId}>
      <div className="settings-row__text">
        <span className="settings-row__title">{title}</span>
        {description ? (
          <span className="settings-row__desc">{description}</span>
        ) : null}
      </div>
      {control !== undefined && control !== null ? (
        <div className="settings-row__control">{control}</div>
      ) : null}
    </div>
  );
}

/** A row that navigates instead of holding a control (`settings-row--nav`). */
export function SettingsNavRow({
  title,
  description,
  onClick,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  onClick: () => void;
  meta?: ReactNode;
}) {
  return (
    <button type="button" className="settings-row settings-row--nav" onClick={onClick}>
      <div className="settings-row__text">
        <span className="settings-row__title">{title}</span>
        {description ? (
          <span className="settings-row__desc">{description}</span>
        ) : null}
      </div>
      {meta !== undefined && meta !== null ? (
        <div className="settings-row__control">{meta}</div>
      ) : null}
    </button>
  );
}

/* ---------------------------------------------------------------- status */

export type SettingsStatusTone =
  | "ok"
  | "warn"
  | "error"
  | "danger"
  | "idle"
  | "accent"
  | "busy";

/**
 * Map the port's tone names onto the upstream CSS modifiers that actually
 * exist (`.settings-status--ok|warn|danger|accent`). `error` is the port's
 * alias for `danger`; `idle`/`busy` have no modifier — they fall through to
 * the plain `.settings-status` (muted dot) default.
 */
const STATUS_TONE_MODIFIER: Record<SettingsStatusTone, string> = {
  ok: "ok",
  warn: "warn",
  error: "danger",
  danger: "danger",
  accent: "accent",
  idle: "",
  busy: "",
};

export function SettingsStatus({
  tone = "idle",
  children,
  title,
}: {
  tone?: SettingsStatusTone;
  children: ReactNode;
  title?: string;
}) {
  const mod = STATUS_TONE_MODIFIER[tone];
  const className = `settings-status${mod ? ` settings-status--${mod}` : ""}`;
  return (
    <span className={className} title={title}>
      <span className="settings-status__dot" aria-hidden="true" />
      {children}
    </span>
  );
}

/** Upstream `renderSettingsValue` — a read-only value cell. */
export function SettingsValue({
  children,
  mono = false,
}: {
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <span className={`settings-row__value${mono ? " settings-row__value--mono" : ""}`}>
      {children}
    </span>
  );
}

/* --------------------------------------------------------- empty/loading */

export function SettingsEmpty({
  children,
  title,
}: {
  children?: ReactNode;
  title?: ReactNode;
}) {
  return (
    <div className="settings-empty">
      {title ? <span className="settings-empty__title">{title}</span> : null}
      {children ? <span className="settings-empty__desc">{children}</span> : null}
    </div>
  );
}

export function SettingsLoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="settings-loading-skeleton" role="status" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="settings-loading-skeleton__row" />
      ))}
    </div>
  );
}

/** Upstream `renderSettingsSummary` — the tile strip (label + number). */
export function SettingsSummary({
  items,
}: {
  items: ReadonlyArray<{ label: string; value: number | string }>;
}) {
  if (items.length === 0) return null;
  return (
    <dl className="settings-summary">
      {items.map((item) => (
        <div className="settings-group settings-summary__tile" key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
