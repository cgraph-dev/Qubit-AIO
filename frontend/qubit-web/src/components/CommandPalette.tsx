/**
 * Ctrl-K command palette for `/qubit-chat`.
 *
 * A modal listbox over a caller-built catalog: page actions, slash commands,
 * reports, and past sessions. It is the keyboard twin of the surface's own
 * voice-commandable affordances — it runs NOTHING itself, it calls back into
 * the page (the page owns `send`, session switching, panel toggles). This keeps
 * it a presentation component: no second chat surface, no new backend route.
 *
 * Accessibility: a labelled dialog wrapping a `role=listbox` of `role=option`
 * rows; the input is the only focusable control and owns ↑/↓/Enter/Esc, so the
 * palette behaves like the composer's slash popover.
 */
import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  movePaletteIndex,
  rankPaletteItems,
  type PaletteItem,
} from "@/lib/commandPalette";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<PaletteItem["kind"], string> = {
  action: "action",
  session: "session",
  command: "command",
  report: "report",
};

export interface CommandPaletteProps {
  open: boolean;
  /** Full catalog for the current page state (built by the host). */
  items: PaletteItem[];
  /** Optional async source label, e.g. "Searching sessions…". */
  loadingLabel?: string;
  onClose: () => void;
  onSelect: (item: PaletteItem) => void;
}

export function CommandPalette({
  open,
  items,
  loadingLabel,
  onClose,
  onSelect,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Reset the query/selection each time the palette opens, and focus the
  // input so the operator can type immediately.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  const ranked = useMemo(() => rankPaletteItems(items, query), [items, query]);
  // Clamp the active row when the filtered list shrinks under it.
  const activeIndex = ranked.length > 0 ? active % ranked.length : 0;

  useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) return null;

  const commit = (item: PaletteItem | undefined) => {
    if (!item || item.disabled) return;
    onSelect(item);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh]"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        data-testid="command-palette"
        className="w-full max-w-lg overflow-hidden rounded-lg border border-border bg-popover text-sm shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-text-tertiary" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            data-testid="command-palette-input"
            placeholder="Run an action, open a session, insert a command…"
            aria-label="Search commands"
            aria-controls="command-palette-listbox"
            aria-activedescendant={
              ranked[activeIndex] ? `palette-${ranked[activeIndex].id}` : undefined
            }
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-text-tertiary"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((i) => movePaletteIndex(i, 1, ranked.length));
                return;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((i) => movePaletteIndex(i, -1, ranked.length));
                return;
              }
              if (event.key === "Enter") {
                event.preventDefault();
                commit(ranked[activeIndex]);
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                onClose();
              }
            }}
          />
          <kbd className="shrink-0 rounded border border-current/20 px-1 text-[0.6875rem] text-text-tertiary">
            Esc
          </kbd>
        </div>

        <div
          id="command-palette-listbox"
          role="listbox"
          aria-label="Palette results"
          className="max-h-80 overflow-y-auto py-1"
          data-testid="command-palette-list"
        >
          {ranked.length === 0 ? (
            <p className="px-3 py-2 text-xs text-text-tertiary">
              {loadingLabel ?? "No matching items."}
            </p>
          ) : (
            ranked.map((item, i) => (
              <button
                key={item.id}
                id={`palette-${item.id}`}
                type="button"
                role="option"
                aria-selected={i === activeIndex}
                data-testid="command-palette-item"
                data-kind={item.kind}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(event) => {
                  // Commit before blur; `commit` closes the palette.
                  event.preventDefault();
                  commit(item);
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left",
                  i === activeIndex && "bg-accent/10",
                )}
              >
                <span className="shrink-0 rounded border border-current/15 px-1 text-[0.625rem] uppercase tracking-wide text-text-tertiary">
                  {KIND_LABEL[item.kind]}
                </span>
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                {item.detail ? (
                  <span className="ml-auto max-w-[45%] shrink-0 truncate text-xs text-text-tertiary">
                    {item.detail}
                  </span>
                ) : null}
              </button>
            ))
          )}
        </div>

        <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[0.6875rem] text-text-tertiary">
          <span>↑↓ navigate</span>
          <span>↵ run</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
