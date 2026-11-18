/* SakugaMultiSelect — the select composition, with checkboxes instead of radios.
 *
 * Same pieces as `SakugaSelect`: a combobox-styled trigger, a panel with a
 * filtering `SakugaInput`, and a list of rows. The rows are `SakugaCheckbox`
 * (multi-select), the panel closes on Escape and outside click, and one hidden
 * input per selected value carries the form data under the shared `name`.
 */
import {
  createElement,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react';
import { SakugaCheckbox, SakugaInput } from '../elements';
import { emitFxAt } from '../fx';
import { DESCRIPTION_STYLE, LABEL_STYLE } from './common';

export interface SakugaMultiSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SakugaMultiSelectProps {
  label: string;
  options: readonly SakugaMultiSelectOption[];
  values?: readonly string[];
  defaultValue?: readonly string[];
  name?: string;
  disabled?: boolean;
  placeholder?: string;
  id?: string;
  className?: string;
  style?: CSSProperties;
  description?: ReactNode;
  onChange?: (values: string[]) => void;
}

const TRIGGER_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 10,
  width: 'var(--sk-input-width, 230px)',
  maxWidth: '100%',
  padding: '7px 12px',
  border: '2.5px solid var(--sk-ink, #1d1a4a)',
  borderRadius: 'var(--sk-r, 15px)',
  background: 'var(--sk-card, #fff)',
  boxShadow: '4px 5px 0 var(--sk-soft, #b8b3ff)',
  color: 'var(--sk-ink, #1d1a4a)',
  fontWeight: 600,
  fontSize: 14,
  lineHeight: 1.35,
  textAlign: 'left',
  cursor: 'pointer',
  fontFamily: 'inherit',
};

const PANEL_STYLE: CSSProperties = {
  position: 'absolute',
  zIndex: 2147482800,
  top: 'calc(100% + 10px)',
  left: 0,
  width: 'min(340px, 92vw)',
  padding: 12,
  border: '2.5px solid var(--sk-ink, #1d1a4a)',
  borderRadius: 'var(--sk-r, 15px)',
  background: 'var(--sk-card, #fff)',
  color: 'var(--sk-ink, #1d1a4a)',
  boxShadow: '4px 5px 0 var(--sk-soft, #b8b3ff)',
};

export function SakugaMultiSelect(props: SakugaMultiSelectProps) {
  const {
    label, options, values, defaultValue, name, disabled,
    placeholder = 'None selected', id, className, style, description, onChange,
  } = props;
  const controlled = values !== undefined;
  const [internal, setInternal] = useState<string[]>(() => Array.from(defaultValue ?? []));
  const selected = controlled ? Array.from(values ?? []) : internal;
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const wrapper = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [filter, options]);
  const summary = useMemo(() => {
    if (!selected.length) return placeholder;
    return options
      .filter((option) => selectedSet.has(option.value))
      .map((option) => option.label)
      .join(', ') || placeholder;
  }, [options, placeholder, selected.length, selectedSet]);

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    setFilter('');
    if (restoreFocus) trigger.current?.focus();
  }, []);

  const toggle = useCallback((value: string) => {
    const next = selectedSet.has(value)
      ? selected.filter((entry) => entry !== value)
      : [...selected, value];
    if (!controlled) setInternal(next);
    onChange?.(next);
    void emitFxAt('tick', trigger.current, { scale: 0.5 });
  }, [controlled, onChange, selected, selectedSet]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) close(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [close, open]);

  const openPanel = () => {
    if (disabled) return;
    setOpen((wasOpen) => !wasOpen);
    if (trigger.current) void emitFxAt('pop', trigger.current, { scale: 0.6 });
  };

  return createElement(
    'div',
    {
      ref: wrapper,
      className: ['qsk-multi', className].filter(Boolean).join(' '),
      id,
      style: { position: 'relative', display: 'inline-block', ...style },
    },
    name
      ? selected.map((value) => createElement('input', {
        key: `hidden-${value}`,
        type: 'hidden',
        name,
        value,
      }))
      : null,
    createElement('span', { style: LABEL_STYLE }, label),
    createElement(
      'button',
      {
        ref: trigger,
        type: 'button',
        role: 'combobox',
        'aria-haspopup': 'listbox',
        'aria-expanded': open ? 'true' : 'false',
        'aria-label': label,
        disabled,
        style: { ...TRIGGER_STYLE, marginTop: 4 },
        onClick: openPanel,
      },
      createElement(
        'span',
        { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
        summary,
      ),
      createElement(
        'span',
        {
          'aria-hidden': 'true',
          style: { transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 120ms' },
        },
        '▾',
      ),
    ),
    open
      ? createElement(
        'div',
        {
          role: 'dialog',
          'aria-label': `${label} options`,
          style: PANEL_STYLE,
          onKeyDown: (event: ReactKeyboardEvent) => {
            if (event.key === 'Tab') close(false);
          },
        },
        createElement(SakugaInput, {
          label: `Filter ${label}`,
          placeholder: 'Filter…',
          value: filter,
          autoFocus: true,
          onChange: setFilter,
          className: 'qsk-select-filter',
        }),
        createElement(
          'div',
          {
            style: {
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              marginTop: 10,
              maxHeight: 260,
              overflowY: 'auto',
            },
          },
          filtered.length
            ? filtered.map((option) => createElement(
              SakugaCheckbox,
              {
                key: option.value,
                checked: selectedSet.has(option.value),
                disabled: option.disabled || disabled,
                onChange: () => toggle(option.value),
              },
              option.label,
            ))
            : createElement('p', { style: { margin: '6px 0 0', ...DESCRIPTION_STYLE } }, 'No matches'),
        ),
      )
      : null,
    description ? createElement('span', { style: DESCRIPTION_STYLE }, description) : null,
  );
}
