import { playLocalFx } from "./localFx";
import { useCallback, useId, useRef, useState, type CSSProperties } from "react";
import { SakugaSelect as OriginalSelect, type SakugaSelectProps } from "@qubit/shared/sakuga";
import { PickerPanel, PickerRow } from "@/manga-skin/PickerPanel";
import { useMangaLayerActive } from "@/manga-skin/useMangaLayerActive";

/**
 * Web-only replacement for SakugaSelect when Q6/Q6-L is enabled.
 * The standard control remains unchanged for every other theme and route.
 * It renders the shared PickerPanel — the same popover the sidebar theme and
 * language pickers use — so all four pickers stay on one design.
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
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const current = controlled ? value : internal;
  const chosen = options.find((opt) => opt.value === current);
  const shown = options.filter((opt) => opt.label.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));

  const close = useCallback((focus = false) => {
    setOpen(false);
    setFilter("");
    if (focus) trigger.current?.focus();
  }, []);

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
          if (trigger.current) playLocalFx("impact", trigger.current, .6);
          setOpen(true);
        }}
      >
        <span className="qm-select-label">{chosen?.label ?? placeholder}</span>
        <span aria-hidden="true" className={open ? "qm-select-chevron qm-select-chevron--open" : "qm-select-chevron"}>▾</span>
      </button>
      {open && (
        <PickerPanel
          id={menuId}
          label={label}
          anchor={trigger}
          filter={filter}
          onFilterChange={setFilter}
          filterLabel={`Filter ${label}`}
          onClose={() => close(true)}
        >
          {shown.length === 0 && (
            <div className="qm-select-empty" role="status">
              {options.length ? "No matches" : "No options available"}
            </div>
          )}
          {shown.map((option) => (
            <PickerRow
              key={option.value}
              selected={option.value === current}
              disabled={option.disabled}
              onSelect={() => select(option.value)}
            >
              <span>{option.label}</span>
            </PickerRow>
          ))}
        </PickerPanel>
      )}
    </span>
  );
}
