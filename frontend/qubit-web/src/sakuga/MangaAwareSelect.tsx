import { createPortal } from "react-dom";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { SakugaSelect as OriginalSelect, type SakugaSelectProps } from "@qubit/shared/sakuga";
import { currentMangaEnabled, mangaRouteEligible, subscribeManga } from "@/manga-skin/manga-store";
import { useSyncExternalStore } from "react";

/**
 * Web-only replacement for SakugaSelect when Q6/Q6-L is enabled.
 * The standard control remains unchanged for every other theme and route.
 * Portal + fixed positioning prevent overflow clipping in chat, Cron and the
 * narrow inspector; viewport clamping keeps every option accessible.
 */
export function MangaAwareSelect(props: SakugaSelectProps) {
  const mangaEnabled = useSyncExternalStore(subscribeManga, currentMangaEnabled, () => false);
  const eligible = typeof window !== "undefined" && mangaRouteEligible(window.location.pathname);
  return mangaEnabled && eligible
    ? <MangaSelect {...props} />
    : <OriginalSelect {...props} />;
}

function MangaSelect({
  label, options, value, defaultValue, name, disabled, placeholder = "Choose…",
  className, style, id, onChange, "data-testid": testId,
}: SakugaSelectProps) {
  const controlled = value !== undefined;
  const [internal, setInternal] = useState(defaultValue ?? "");
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [placement, setPlacement] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const current = controlled ? value : internal;
  const chosen = options.find((opt) => opt.value === current);
  const shown = options.filter((opt) => opt.label.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));

  const close = useCallback((focus = false) => {
    setOpen(false);
    setFilter("");
    if (focus) trigger.current?.focus();
  }, []);

  const reposition = useCallback(() => {
    const box = trigger.current?.getBoundingClientRect();
    if (!box) return;
    const margin = 12;
    const gap = 6;
    const maxWidth = Math.max(190, window.innerWidth - margin * 2);
    const width = Math.min(360, maxWidth);
    const left = Math.max(margin, Math.min(box.left, window.innerWidth - margin - width));
    const availableBelow = window.innerHeight - box.bottom - gap - margin;
    const availableAbove = box.top - gap - margin;
    const up = availableBelow < 260 && availableAbove > availableBelow;
    const available = Math.max(120, up ? availableAbove : availableBelow);
    const maxHeight = Math.min(420, available);
    setPlacement({
      position: "fixed",
      left,
      width,
      maxHeight,
      ...(up ? { bottom: window.innerHeight - box.top + gap, top: "auto" } : { top: box.bottom + gap, bottom: "auto" }),
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    const onScroll = (e: Event) => {
      if (panel.current?.contains(e.target as Node)) return;
      reposition();
    };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!trigger.current?.contains(target) && !panel.current?.contains(target)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close(true); }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    const focusFrame = requestAnimationFrame(() => filterRef.current?.focus());
    return () => {
      cancelAnimationFrame(focusFrame);
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  const select = (next: string) => {
    if (!controlled) setInternal(next);
    onChange?.(next);
    close(true);
  };

  return (
    <span className={["qm-select-root", className].filter(Boolean).join(" ")} id={id} style={style} data-testid={testId}>
      {name && <input type="hidden" name={name} value={current ?? ""} />}
      <button
        ref={trigger}
        type="button"
        className="qm-select-trigger"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={() => { if (!open) reposition(); setOpen((v) => !v); }}
      >
        <span className="qm-select-label">{chosen?.label ?? placeholder}</span>
        <span aria-hidden="true" className={open ? "qm-select-chevron qm-select-chevron--open" : "qm-select-chevron"}>▾</span>
      </button>
      {open && typeof document !== "undefined" && createPortal(
        <div
          ref={panel}
          id={menuId}
          className="qm-select-popover"
          role="group"
          aria-label={label}
          style={placement}
        >
          <div className="qm-select-popover__top">
            <span aria-hidden="true" className="qm-select-popover__spark">✦</span>
            <input
              ref={filterRef}
              className="qm-select-filter"
              aria-label={`Filter ${label}`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter…"
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  panel.current?.querySelector<HTMLButtonElement>('[role="option"]:not(:disabled)')?.focus();
                }
              }}
            />
          </div>
          <div className="qm-select-popover__options" role="listbox" aria-label={label}>
            {shown.length === 0 && <div className="qm-select-empty">No matches</div>}
            {shown.map((option, index) => (
              <button
                type="button"
                className="qm-select-option"
                role="option"
                aria-selected={option.value === current}
                key={option.value}
                disabled={option.disabled}
                onClick={() => select(option.value)}
                onKeyDown={(e) => {
                  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
                  e.preventDefault();
                  const enabled = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? []);
                  const at = enabled.indexOf(e.currentTarget);
                  enabled[(at + (e.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length]?.focus();
                }}
              >
                <span className="qm-select-radio" aria-hidden="true">{option.value === current ? "●" : ""}</span>
                <span>{option.label}</span>
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </span>
  );
}
