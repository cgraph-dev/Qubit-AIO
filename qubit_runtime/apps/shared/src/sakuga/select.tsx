/* SakugaSelect — the composition the owner asked for.
 *
 * Sakuga VFX ships no `<select>`: the twelve components are button, toggle,
 * slider, checkbox, radio, input, tabs, progress, rating, toast and tooltip.
 * So a select is built from what does exist: a trigger that looks like the
 * Sakuga input, and a panel that appears on click with a filter input and a
 * radio group for the values. It is Qubit-owned UI, documented as such in
 * THIRD_PARTY_NOTICES.md; it is not presented as upstream code.
 *
 * The trigger is a real `<button role="combobox">`, the panel is labelled, the
 * filter is a real text input, the options are real radio inputs, Escape
 * closes, and a hidden input carries the value into forms.
 */
import {
  createElement,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { SakugaInput, SakugaRadio, type SakugaRadioOption } from './elements';
import { emitFxAt } from './fx';

export interface SakugaSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SakugaSelectProps {
  label: string;
  options: readonly SakugaSelectOption[];
  value?: string;
  defaultValue?: string;
  name?: string;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  style?: CSSProperties;
  id?: string;
  'data-testid'?: string;
  onChange?: (value: string) => void;
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

export function SakugaSelect(props: SakugaSelectProps) {
  const {
    label, options, value, defaultValue, name, disabled, placeholder = 'Choose…',
    className, style, id, onChange,
  } = props;
  const controlled = value !== undefined;
  const [internal, setInternal] = useState(defaultValue ?? '');
  const current = controlled ? value : internal;
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const wrapper = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const listId = useId();

  const selected = options.find((option) => option.value === current);
  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [filter, options]);

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    setFilter('');
    if (restoreFocus) trigger.current?.focus();
  }, []);

  const choose = useCallback((next: string) => {
    if (!controlled) setInternal(next);
    onChange?.(next);
    close();
  }, [close, controlled, onChange]);

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
    if (trigger.current) void emitFxAt('impact', trigger.current, { scale: 0.6 });
  };

  return createElement(
    'div',
    {
      ref: wrapper,
      className,
      id,
      style: { position: 'relative', display: 'inline-block', ...style },
      'data-testid': props['data-testid'],
    },
    name
      ? createElement('input', { type: 'hidden', name, value: current ?? '' })
      : null,
    createElement(
      'button',
      {
        ref: trigger,
        type: 'button',
        role: 'combobox',
        'aria-haspopup': 'listbox',
        'aria-expanded': open ? 'true' : 'false',
        'aria-controls': open ? listId : undefined,
        'aria-label': label,
        'aria-disabled': disabled ? 'true' : undefined,
        disabled,
        style: TRIGGER_STYLE,
        onClick: openPanel,
      },
      createElement('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
        selected?.label ?? placeholder),
      createElement('span', { 'aria-hidden': 'true', style: { transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 120ms' } }, '▾'),
    ),
    open
      ? createElement(
        'div',
        {
          id: listId,
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
          onChange: setFilter,
        }),
        filtered.length
          ? createElement(SakugaRadio, {
            label,
            name: `${listId}-options`,
            options: filtered.map((option): SakugaRadioOption => ({
              value: option.value,
              label: option.label,
              selected: option.value === current,
              disabled: option.disabled,
            })),
            value: current,
            onChange: choose,
          })
          : createElement('p', { style: { margin: '10px 0 0', color: 'var(--sk-dim, #8580b0)' } }, 'No matches'),
      )
      : null,
  );
}
