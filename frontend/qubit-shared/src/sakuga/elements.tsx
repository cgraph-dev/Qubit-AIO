/* React wrappers around the vendored Sakuga VFX custom elements.
 *
 * The upstream components are shadow-DOM custom elements with real native
 * controls inside (a shadow `<button>`, `<input type="checkbox">`, a real range
 * input, …), so they keep keyboard and screen-reader behaviour. These wrappers
 * only:
 *   - load the component file on first render (lazy; the toggle is 506 KB),
 *   - forward the upstream attributes,
 *   - mirror the accessible role/label onto the light-DOM host so the rest of
 *     Qubit can query it without piercing the shadow root,
 *   - expose the imperative methods the upstream README documents.
 *
 * They deliberately do NOT re-implement any drawing: `core.js` is the engine.
 */
import {
  createElement,
  forwardRef,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type CSSProperties,
  type FocusEventHandler,
  type KeyboardEventHandler,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type MouseEventHandler,
  type ReactNode,
  type Ref,
} from 'react';
import { loadSakugaComponent, sakugaTag } from './loader';
import type { SakugaComponentName } from './types';
import { emitFxAt, type FxCue } from './fx';

/** Host attributes every wrapper accepts in addition to its own. */
export interface SakugaHostProps {
  id?: string;
  title?: string;
  className?: string;
  style?: CSSProperties;
  role?: string;
  tabIndex?: number;
  onClick?: MouseEventHandler<HTMLElement>;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
  onFocus?: FocusEventHandler<HTMLElement>;
  onBlur?: FocusEventHandler<HTMLElement>;
  'aria-label'?: string;
  'aria-describedby'?: string;
  'aria-labelledby'?: string;
  'aria-hidden'?: boolean;
  'data-testid'?: string;
}

type AttrValue = string | number | boolean | undefined | null;

const HOST_KEYS = new Set([
  'id',
  'title',
  'class',
  'className',
  'style',
  'role',
  'tabIndex',
  'onClick',
  'onKeyDown',
  'onFocus',
  'onBlur',
]);

function hostProps(props: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || key === 'children') continue;
    if (HOST_KEYS.has(key) || key.startsWith('aria-') || key.startsWith('data-')) {
      out[key === 'className' ? 'class' : key] = value;
    }
  }
  return out;
}

function isDefined(tag: string): boolean {
  return typeof window !== 'undefined' && Boolean(window.customElements?.get(tag));
}

function writeValue(el: HTMLElement, key: string, value: AttrValue): void {
  const element = el as unknown as Record<string, unknown>;
  const defined = isDefined(el.tagName.toLowerCase());
  const live = defined ? element[key] : undefined;
  if (
    (key === 'value' || key === 'checked' || key === 'selected' || key === 'disabled')
    && defined
    && typeof live !== 'undefined'
  ) {
    if (value === false || value === undefined || value === null) {
      if (key === 'value' || key === 'selected') el.setAttribute(key, '');
      else el.removeAttribute(key);
      if (typeof live === 'boolean') element[key] = false;
      return;
    }
    const next = typeof live === 'boolean' ? Boolean(value) : value;
    if (live !== next) element[key] = next;
    if (typeof next === 'boolean') {
      if (next) el.setAttribute(key, '');
      else el.removeAttribute(key);
    } else {
      el.setAttribute(key, String(next));
    }
    return;
  }
  if (value === undefined || value === null || value === false) el.removeAttribute(key);
  else el.setAttribute(key, value === true ? '' : String(value));
}

function useSakuga(
  name: SakugaComponentName,
  ref: Ref<HTMLElement | null>,
  attrs: Record<string, AttrValue>,
  initialAttrs: Record<string, AttrValue> = {},
): void {
  useEffect(() => {
    void loadSakugaComponent(name).catch(() => {
      // A failed component must leave the rest of the page usable; the element
      // stays inert and the host still carries its label/role.
    });
  }, [name]);

  const first = useRef(true);
  useLayoutEffect(() => {
    const el = ref && 'current' in ref ? (ref as { current: HTMLElement | null }).current : null;
    if (!el) return;
    if (first.current) {
      first.current = false;
      for (const [key, value] of Object.entries(initialAttrs)) writeValue(el, key, value);
    }
    for (const [key, value] of Object.entries(attrs)) {
      // An uncontrolled control owns its value after mount; do not write it
      // back on every render or the user's edit is lost.
      if (value === undefined) continue;
      writeValue(el, key, value);
    }
  });
}

/* ─────────────────────────────── Button ─────────────────────────────── */

export interface SakugaButtonProps extends SakugaHostProps {
  children?: ReactNode;
  variant?: 'primary' | 'secondary';
  type?: 'button' | 'submit' | 'reset';
  disabled?: boolean;
  /** Lands the pink check (call only when the action actually worked). */
  done?: boolean;
  /** Effect fired on press. Defaults to `impact`. */
  fx?: FxCue | false;
  name?: string;
  value?: string;
}

export const SakugaButton = forwardRef<HTMLElement, SakugaButtonProps>(function SakugaButton(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const {
    children, variant = 'primary', type = 'button', disabled = false,
    done = false, fx = 'impact', className, onClick, name, value, ...rest
  } = props;

  useSakuga('button', inner, {
    variant: variant === 'secondary' ? 'secondary' : undefined,
    type: type === 'button' ? undefined : type,
    disabled,
    name,
    value,
  });

  useEffect(() => {
    if (!done) return;
    const el = inner.current as unknown as { done?: () => void } | null;
    el?.done?.();
  }, [done]);

  const handleClick = useCallback((event: ReactMouseEvent<HTMLElement>) => {
    if (fx) void emitFxAt(fx, event.currentTarget);
    onClick?.(event);
  }, [fx, onClick]);

  return createElement(
    'sakuga-button',
    {
      ...hostProps(rest),
      ref: (node: HTMLElement | null) => {
        inner.current = node;
        if (typeof ref === 'function') ref(node);
        else if (ref) (ref as { current: HTMLElement | null }).current = node;
      },
      class: className,
      onClick: handleClick,
      role: rest.role ?? 'button',
    },
    children,
  );
});

/* ─────────────────────────────── Toggle ─────────────────────────────── */

export interface SakugaToggleProps extends SakugaHostProps {
  label: string;
  checked?: boolean;
  defaultChecked?: boolean;
  name?: string;
  value?: string;
  disabled?: boolean;
  onChange?: (checked: boolean) => void;
}

export const SakugaToggle = forwardRef<HTMLElement, SakugaToggleProps>(function SakugaToggle(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { label, checked, defaultChecked, name, value, disabled, onChange, className, ...rest } = props;
  const current = checked ?? defaultChecked ?? false;
  const controlled = checked !== undefined;

  useSakuga(
    'toggle',
    inner,
    { label, checked: controlled ? checked : undefined, name, value, disabled },
    { checked: current },
  );

  return createElement('sakuga-toggle', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'switch',
    'aria-checked': current ? 'true' : 'false',
    'aria-label': label,
    onChange: () => onChange?.((inner.current as unknown as { checked?: boolean } | null)?.checked ?? false),
  });
});

/* ────────────────────────────── Checkbox ────────────────────────────── */

export interface SakugaCheckboxProps extends SakugaHostProps {
  children?: ReactNode;
  checked?: boolean;
  defaultChecked?: boolean;
  name?: string;
  value?: string;
  disabled?: boolean;
  onChange?: (checked: boolean) => void;
}

export const SakugaCheckbox = forwardRef<HTMLElement, SakugaCheckboxProps>(function SakugaCheckbox(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { children, checked, defaultChecked, name, value, disabled, onChange, className, ...rest } = props;
  const current = checked ?? defaultChecked ?? false;
  const controlled = checked !== undefined;

  useSakuga(
    'checkbox',
    inner,
    { checked: controlled ? checked : undefined, name, value, disabled },
    { checked: current },
  );

  return createElement('sakuga-checkbox', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'checkbox',
    'aria-checked': current ? 'true' : 'false',
    onChange: () => onChange?.((inner.current as unknown as { checked?: boolean } | null)?.checked ?? false),
  }, children);
});

/* ─────────────────────────────── Radio ──────────────────────────────── */

export interface SakugaRadioOption {
  value: string;
  label: string;
  selected?: boolean;
  disabled?: boolean;
}

export interface SakugaRadioProps extends SakugaHostProps {
  label: string;
  options: readonly SakugaRadioOption[];
  value?: string;
  name?: string;
  disabled?: boolean;
  onChange?: (value: string) => void;
}

export const SakugaRadio = forwardRef<HTMLElement, SakugaRadioProps>(function SakugaRadio(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { label, options, value, name, disabled, onChange, className, ...rest } = props;
  const signature = options.map((o) => `${o.value}:${o.label}`).join('|');
  const selected = value ?? options.find((o) => o.selected)?.value;

  useSakuga('radio', inner, { label, value: selected, name, disabled });

  return createElement('sakuga-radio', {
    ...hostProps(rest),
    // The upstream component reads its options once. A changed option set must
    // remount, or the visible rows would silently disagree with the props.
    key: signature,
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'radiogroup',
    'aria-label': label,
    onChange: () => onChange?.((inner.current as unknown as { value?: string } | null)?.value ?? ''),
  }, options.map((option) => createElement('option', {
    key: option.value,
    value: option.value,
    selected: option.selected,
    disabled: option.disabled,
  }, option.label)));
});

/* ─────────────────────────────── Input ──────────────────────────────── */

export interface SakugaInputProps extends SakugaHostProps {
  type?: 'text' | 'email' | 'search' | 'url' | 'tel' | 'password';
  label: string;
  placeholder?: string;
  value?: string;
  defaultValue?: string;
  name?: string;
  disabled?: boolean;
  autoComplete?: string;
  onChange?: (value: string) => void;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
  onEnter?: () => void;
}

export const SakugaInput = forwardRef<HTMLElement, SakugaInputProps>(function SakugaInput(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const {
    type = 'text', label, placeholder, value, defaultValue, name, disabled,
    autoComplete, onChange, onKeyDown, onEnter, className, ...rest
  } = props;
  const controlled = value !== undefined;

  useSakuga(
    'input',
    inner,
    {
      type, label, placeholder, value: controlled ? value : undefined, name, disabled,
      autocomplete: autoComplete,
    },
    { value: defaultValue ?? '' },
  );

  return createElement('sakuga-input', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'textbox',
    // An explicit caller label wins; otherwise the visible field label is the
    // accessible name.
    'aria-label': (rest['aria-label'] as string | undefined) ?? label,
    'data-sakuga-input': '1',
    onInput: (event: Event) => {
      const target = event.target as HTMLElement | null;
      const el = inner.current as unknown as { value?: string } | null;
      onChange?.(el?.value ?? target?.getAttribute('value') ?? '');
    },
    onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => {
      onKeyDown?.(event);
      if (event.key === 'Enter') onEnter?.();
    },
  });
});

/* ─────────────────────────────── Slider ─────────────────────────────── */

export interface SakugaSliderProps extends SakugaHostProps {
  label: string;
  value?: number | string;
  defaultValue?: number | string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  name?: string;
  disabled?: boolean;
  onChange?: (value: number) => void;
}

export const SakugaSlider = forwardRef<HTMLElement, SakugaSliderProps>(function SakugaSlider(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { label, value, defaultValue, min = 0, max = 100, step = 1, unit, name, disabled, onChange, className, ...rest } = props;
  useSakuga(
    'slider',
    inner,
    { label, value: value !== undefined ? value : undefined, min, max, step, unit, name, disabled },
    { value: defaultValue ?? 50 },
  );
  return createElement('sakuga-slider', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'slider',
    'aria-label': label,
    onChange: () => onChange?.((inner.current as unknown as { valueAsNumber?: number } | null)?.valueAsNumber ?? 0),
  });
});

/* ──────────────────────────────── Tabs ──────────────────────────────── */

export interface SakugaTabOption {
  value: string;
  label: string;
}

export interface SakugaTabsProps extends SakugaHostProps {
  label: string;
  options: readonly SakugaTabOption[];
  selected?: number;
  onChange?: (detail: { index: number; value: string; label: string }) => void;
}

export const SakugaTabs = forwardRef<HTMLElement, SakugaTabsProps>(function SakugaTabs(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { label, options, selected = 0, onChange, className, ...rest } = props;
  const signature = options.map((o) => `${o.value}:${o.label}`).join('|');
  useSakuga('tabs', inner, { label, selected });
  return createElement('sakuga-tabs', {
    ...hostProps(rest),
    key: signature,
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'tablist',
    'aria-label': label,
    onChange: (event: Event) => {
      // React wraps the native `change` in a synthetic event on a custom host,
      // so the CustomEvent payload lives on `nativeEvent.detail`.
      const custom = event as CustomEvent<{ index: number; value: string; label: string }>;
      const detail = custom.detail
        ?? (custom as unknown as { nativeEvent?: CustomEvent<{ index: number; value: string; label: string }> })
          .nativeEvent?.detail;
      if (detail) onChange?.(detail);
    },
  }, options.map((option) => createElement('option', { key: option.value, value: option.value }, option.label)));
});

/* ─────────────────────────────── Progress ───────────────────────────── */

export interface SakugaProgressProps extends SakugaHostProps {
  label: string;
  value?: number;
  doneLabel?: string;
  onDone?: () => void;
}

export const SakugaProgress = forwardRef<HTMLElement, SakugaProgressProps>(function SakugaProgress(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { label, value = 0, doneLabel, onDone, className, ...rest } = props;
  useSakuga('progress', inner, { label, value: Math.max(0, Math.min(1, value)), 'done-label': doneLabel });
  useEffect(() => {
    const el = inner.current;
    if (!el) return;
    const listener = () => onDone?.();
    el.addEventListener('sakuga:done', listener);
    return () => el.removeEventListener('sakuga:done', listener);
  }, [onDone]);
  return createElement('sakuga-progress', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'progressbar',
    'aria-label': label,
    'aria-valuemin': 0,
    'aria-valuemax': 100,
    'aria-valuenow': Math.round(Math.max(0, Math.min(1, value)) * 100),
  });
});

/* ──────────────────────────────── Rating ────────────────────────────── */

export interface SakugaRatingProps extends SakugaHostProps {
  label: string;
  value?: number;
  name?: string;
  disabled?: boolean;
  onChange?: (value: number) => void;
}

export const SakugaRating = forwardRef<HTMLElement, SakugaRatingProps>(function SakugaRating(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { label, value = 0, name, disabled, onChange, className, ...rest } = props;
  useSakuga('rating', inner, { label, value, name, disabled });
  return createElement('sakuga-rating', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'radiogroup',
    'aria-label': label,
    onChange: () => onChange?.((inner.current as unknown as { value?: number } | null)?.value ?? 0),
  });
});

/* ─────────────────────────────── Tooltip ────────────────────────────── */

export interface SakugaTooltipProps extends SakugaHostProps {
  text: string;
  label?: string;
  children?: ReactNode;
}

export const SakugaTooltip = forwardRef<HTMLElement, SakugaTooltipProps>(function SakugaTooltip(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const { text, label = 'Help', children, className, ...rest } = props;
  useSakuga('tooltip', inner, { text, label });
  return createElement('sakuga-tooltip', {
    ...hostProps(rest),
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    role: rest.role ?? 'button',
    'aria-label': label,
  }, children);
});

/* ──────────────────────────────── Toast ─────────────────────────────── */

export interface SakugaToastOptions {
  duration?: number;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';
}

let toastElement: HTMLElement | null = null;

/** Show one toast. Upstream supports one at a time; a later call replaces it. */
export function showSakugaToast(message: string, options: SakugaToastOptions = {}): void {
  if (typeof document === 'undefined') return;
  if (!toastElement || !toastElement.isConnected) {
    toastElement = document.createElement('sakuga-toast');
    const position = options.position ?? 'bottom-left';
    const vertical = position.startsWith('top') ? 'top: 24px' : 'bottom: 24px';
    const horizontal = position.endsWith('right') ? 'right: 24px' : 'left: 24px';
    toastElement.setAttribute('style', `position: fixed; ${vertical}; ${horizontal}; z-index: 2147482900;`);
    document.body.append(toastElement);
    void loadSakugaComponent('toast').catch(() => {});
  }
  if (options.duration) toastElement.setAttribute('duration', String(options.duration));
  const el = toastElement as unknown as { show?: (text?: string) => void };
  el.show?.(message);
}

/** Mount once near the app root so the toast element exists before first use. */
export function SakugaToastHost(): null {
  useEffect(() => {
    void loadSakugaComponent('toast').catch(() => {});
  }, []);
  return null;
}

/* Re-exported for consumers that need the tag name. */
export { sakugaTag };
