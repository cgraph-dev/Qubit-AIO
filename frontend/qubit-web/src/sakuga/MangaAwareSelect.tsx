import { createPortal } from "react-dom";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { SakugaSelect as OriginalSelect, type SakugaSelectProps } from "@qubit/shared/sakuga";
import { panelPlacement, visibleViewport } from "@/manga-skin/panelPlacement";
import { useMangaLayerActive } from "@/manga-skin/useMangaLayerActive";

/**
 * Web-only replacement for SakugaSelect when Q6/Q6-L is enabled.
 * The standard control remains unchanged for every other theme and route.
 * Portal + fixed positioning prevent overflow clipping in chat, Cron and the
 * narrow inspector; viewport clamping keeps every option accessible.
 */
export function MangaAwareSelect(props: SakugaSelectProps) {
  const layerActive = useMangaLayerActive();
  return layerActive ? <MangaSelect {...props} /> : <OriginalSelect {...props} />;
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
  const [portalHost, setPortalHost] = useState<HTMLElement | null>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const listId = `${menuId}-listbox`;
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
    const viewport = visibleViewport();
    const dimensions = panelPlacement(box, viewport, { width: 360, height: 420 });
    const element = panel.current;
    const body = element?.querySelector(".qm-select-popover__options");
    const height = element && body
      ? Math.min(dimensions.maxHeight, element.offsetHeight + body.scrollHeight - body.clientHeight)
      : dimensions.maxHeight;
    const { top } = panelPlacement(box, viewport, { width: dimensions.width, height });
    setPlacement({ position: "fixed", ...dimensions, top });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    const onScroll = (e: Event) => {
      if (panel.current?.contains(e.target as Node)) return;
      reposition();
    };
    window.addEventListener("resize", reposition);
    window.visualViewport?.addEventListener("resize", reposition);
    window.visualViewport?.addEventListener("scroll", reposition);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.visualViewport?.removeEventListener("resize", reposition);
      window.visualViewport?.removeEventListener("scroll", reposition);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, reposition, shown.length]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!trigger.current?.contains(target) && !panel.current?.contains(target)) close();
    };
    document.addEventListener("pointerdown", onPointer);
    const focusFrame = window.setTimeout(() => filterRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(focusFrame);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open, close]);

  const select = (next: string) => {
    if (!controlled) setInternal(next);
    onChange?.(next);
    close(true);
  };

  return (
    <span className={["qm-select-root", className].filter(Boolean).join(" ")} style={style as unknown as CSSProperties} data-testid={testId}>
      {name && <input type="hidden" name={name} value={current ?? ""} />}
      <button
        ref={trigger}
        id={id}
        type="button"
        className="qm-select-trigger"
        aria-label={`${label}: ${chosen?.label ?? placeholder}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={() => {
          if (open) { close(); return; }
          // Keep keyboard focus inside the owning modal, even though the
          // options are painted outside its fields and scrollable body.
          setPortalHost(trigger.current?.closest<HTMLElement>('[aria-modal="true"]') ?? document.body);
          reposition();
          setOpen(true);
        }}
      >
        <span className="qm-select-label">{chosen?.label ?? placeholder}</span>
        <span aria-hidden="true" className={open ? "qm-select-chevron qm-select-chevron--open" : "qm-select-chevron"}>▾</span>
      </button>
      {open && portalHost && createPortal(
        <div
          ref={panel}
          id={menuId}
          className="qm-select-popover"
          role="dialog"
          data-qm-panel=""
          onBlur={(event) => {
            const next = event.relatedTarget;
            if (next && !event.currentTarget.contains(next) && next !== trigger.current) close();
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            close(true);
          }}
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
          {shown.length === 0 && <div className="qm-select-empty" role="status">{options.length ? "No matches" : "No options available"}</div>}
          <div
            id={listId}
            data-qm-panel-options=""
            className="qm-select-popover__options"
            role="listbox"
            aria-label={label}
          >
            {shown.map((option) => (
              <button
                type="button"
                className="qm-select-option"
                role="option"
                tabIndex={-1}
                aria-selected={option.value === current}
                key={option.value}
                disabled={option.disabled}
                onClick={() => select(option.value)}
                onKeyDown={(e) => {
                  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
                  e.preventDefault();
                  const enabled = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? []);
                  const at = enabled.indexOf(e.currentTarget);
                  if (e.key === "ArrowUp" && at === 0) {
                    filterRef.current?.focus();
                    return;
                  }
                  const next = e.key === "Home" ? 0 : e.key === "End" ? enabled.length - 1
                    : (at + (e.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
                  enabled[next]?.focus();
                }}
              >
                <span className="qm-select-radio" aria-hidden="true">{option.value === current ? "●" : ""}</span>
                <span>{option.label}</span>
              </button>
            ))}
          </div>
        </div>,
        portalHost,
      )}
    </span>
  );
}
