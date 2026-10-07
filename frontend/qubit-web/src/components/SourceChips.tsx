/**
 * Inline source chips for a `/qubit-chat` answer.
 *
 * Copilot's defining UI idea is "show where the answer came from"; a report
 * surface lives on that. The transcript already carries a `sources: string[]`
 * per message (persisted `meta.sources`, set from the web-branch done receipt),
 * but it rendered as a raw comma-joined URL run. This renders each source as a
 * numbered, host-labelled chip so a reader can scan provenance at a glance —
 * keyboard reachable, external links opened with `rel=noreferrer`.
 *
 * Presentation only: it never fetches or resolves a source, so it cannot
 * introduce a network path or a second data model.
 */
import { ExternalLink } from "lucide-react";

/** A short label for a source: bare host+path, or the raw token. */
export function sourceLabel(raw: string): string {
  const trimmed = raw.trim();
  try {
    const url = new URL(trimmed);
    const rest = `${url.pathname}${url.search}`.replace(/\/$/, "");
    return rest && rest !== "/" ? `${url.host}${rest}` : url.host;
  } catch {
    return trimmed;
  }
}

/** True when a source is an http(s) URL that is safe to open in a new tab. */
export function isExternalSource(raw: string): boolean {
  return /^https?:\/\//i.test(raw.trim());
}

export function SourceChips({ sources }: { sources: string[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <ul
      data-testid="message-sources"
      aria-label="Sources"
      className="mt-1.5 flex flex-wrap items-center gap-1 text-xs"
    >
      <li className="font-semibold text-text-tertiary" aria-hidden="true">
        Sources
      </li>
      {sources.map((src, i) => {
        const external = isExternalSource(src);
        const label = sourceLabel(src);
        const inner = (
          <>
            <span className="font-mono opacity-60">[{i + 1}]</span>
            <span className="truncate">{label}</span>
            {external ? <ExternalLink className="h-3 w-3 shrink-0" /> : null}
          </>
        );
        const cls =
          "inline-flex max-w-[16rem] items-center gap-1 rounded-full border border-current/20 " +
          "bg-background-base/60 px-2 py-0.5 text-text-secondary" +
          (external ? " hover:bg-midground/10 hover:text-foreground" : "");
        return (
          <li key={`${i}-${src}`} className="min-w-0">
            {external ? (
              <a
                href={src}
                target="_blank"
                rel="noreferrer"
                title={src}
                className={cls}
              >
                {inner}
              </a>
            ) : (
              <span title={src} className={cls}>
                {inner}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default SourceChips;
