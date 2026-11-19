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
  useState,
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
  onDoubleClick?: MouseEventHandler<HTMLElement>;
  onMouseEnter?: MouseEventHandler<HTMLElement>;
  onMouseLeave?: MouseEventHandler<HTMLElement>;
  onContextMenu?: MouseEventHandler<HTMLElement>;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
  onFocus?: FocusEventHandler<HTMLElement>;
  onBlur?: FocusEventHandler<HTMLElement>;
  'aria-label'?: string;
  'aria-describedby'?: string;
  'aria-labelledby'?: string;
  // These mirror the native ARIA token unions, so spreading
  // React.ComponentProps<'button'> (which carries the full AriaAttributes)
  // into a wrapper stays assignable.
  'aria-invalid'?: boolean | 'true' | 'false' | 'grammar' | 'spelling';
  'aria-required'?: boolean | 'true' | 'false';
  'aria-selected'?: boolean | 'true' | 'false';
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
    if (HOST_KEYS.has(key) || key.startsWith('on') || key.startsWith('aria-') || key.startsWith('data-')) {
      // React 19 writes a boolean attribute on a custom element as an empty
      // string. ARIA/data values are read by tests and CSS, so keep the
      // literal 'true'/'false'.
      out[key === 'className' ? 'class' : key] =
        typeof value === 'boolean' && (key.startsWith('aria-') || key.startsWith('data-'))
          ? String(value)
          : value;
    }
  }
  return out;
}

function hostShadowInput(host: HTMLElement | null): HTMLInputElement | null {
  return host?.shadowRoot?.querySelector<HTMLInputElement>('input') ?? null;
}

const INNER_INPUT_METHODS = [
  'select',
  'setSelectionRange',
  'setRangeText',
  'focus',
  'blur',
  'checkValidity',
  'reportValidity',
  'setCustomValidity',
] as const;

const INNER_INPUT_PROPS = [
  'type',
  'placeholder',
  'required',
  'pattern',
  'minLength',
  'maxLength',
  'readOnly',
  'selectionStart',
  'selectionEnd',
] as const;

/** Let call sites treat the host like the native input it wraps. */
function delegateInnerInput(host: HTMLElement): void {
  const marked = host as HTMLElement & { __sakugaDelegated?: boolean };
  if (marked.__sakugaDelegated) return;
  marked.__sakugaDelegated = true;
  const element = host as unknown as Record<string, unknown>;
  for (const method of INNER_INPUT_METHODS) {
    Object.defineProperty(host, method, {
      configurable: true,
      value: (...args: unknown[]) => {
        const input = hostShadowInput(host) as unknown as Record<string, (...a: unknown[]) => unknown> | null;
        return input?.[method]?.(...args);
      },
    });
  }
  for (const prop of INNER_INPUT_PROPS) {
    if (Object.prototype.hasOwnProperty.call(host, prop)) continue;
    Object.defineProperty(host, prop, {
      configurable: true,
      get: () => {
        const input = hostShadowInput(host) as unknown as Record<string, unknown> | null;
        if (input) return input[prop];
        return host.getAttribute(prop.toLowerCase()) ?? undefined;
      },
    });
  }
  element['valueAsNumber'] = undefined;
  Object.defineProperty(host, 'valueAsNumber', {
    configurable: true,
    get: () => {
      const input = hostShadowInput(host) as unknown as { valueAsNumber?: number } | null;
      return input?.valueAsNumber;
    },
  });
}

function isDefined(tag: string): boolean {
  return typeof window !== 'undefined' && Boolean(window.customElements?.get(tag));
}

/** A property can be assigned only when its descriptor has a setter (or is a data property). */
function writableProperty(el: HTMLElement, key: string): boolean {
  let proto: object | null = el;
  while (proto) {
    const descriptor = Object.getOwnPropertyDescriptor(proto, key);
    if (descriptor) {
      return 'set' in descriptor ? typeof descriptor.set === 'function' : descriptor.writable === true;
    }
    proto = Object.getPrototypeOf(proto);
  }
  return false;
}

/**
 * Sync one value onto the host, mutating only what actually changes.
 *
 * `setAttribute` fires `attributeChangedCallback` even when the new value is
 * identical. The vendored controls treat that callback as a real change: the
 * toggle resets `seq` to a one-frame jump when `checked` is rewritten, so a
 * redundant write on every React render killed its 43-frame ON animation.
 * (Removing an absent attribute is a no-op, which is why OFF appeared to work.)
 *
 * The writable-property guard matters too: progress exposes a read-only
 * `value` getter, and assigning it throws in strict mode. Its attribute is the
 * documented input, so the attribute path still drives it.
 */
function writeValue(el: HTMLElement, key: string, value: AttrValue): void {
  const booleanKey = key === 'checked' || key === 'disabled';
  const clearing = value === undefined || value === null || value === false;
  const nextAttr = clearing ? null : value === true ? '' : String(value);

  if (isDefined(el.tagName.toLowerCase())) {
    const element = el as unknown as Record<string, unknown>;
    const live = element[key];
    if (typeof live !== 'undefined' && writableProperty(el, key)) {
      if (clearing) {
        if (typeof live === 'boolean' && live) element[key] = false;
      } else {
        const nextProp = booleanKey ? Boolean(value) : value;
        if (live !== nextProp) element[key] = nextProp;
      }
    }
  }

  if (nextAttr === null) {
    if (el.hasAttribute(key)) el.removeAttribute(key);
  } else if (el.getAttribute(key) !== nextAttr) {
    el.setAttribute(key, nextAttr);
  }
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
    if (fx) {
      const hasPointer = event.clientX !== 0 || event.clientY !== 0;
      const point = hasPointer
        ? {
            x: event.clientX + (typeof window !== 'undefined' ? window.scrollX : 0),
            y: event.clientY + (typeof window !== 'undefined' ? window.scrollY : 0),
          }
        : undefined;
      void emitFxAt(fx, event.currentTarget, point);
    }
    onClick?.(event);
  }, [fx, onClick]);

  return createElement(
    'sakuga-button',
    {
      ...hostProps(rest),
      // `variant` and `type` are read once, in the element's first render, and
      // the upstream element does not observe them. A layout-effect write is
      // too late: the custom element has already connected and rendered its
      // shadow DOM, so a `variant="secondary"` button stayed primary. Passing
      // them here sets the attributes before React inserts the node, which is
      // before `connectedCallback`.
      variant: variant === 'secondary' ? 'secondary' : undefined,
      type: type === 'button' ? undefined : type,
      disabled: disabled ? '' : undefined,
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
    // `label` is only read at render time and is not an observed attribute, so
    // it must be an initial attribute too (and a change needs the remount).
    key: `${signature}|${label}`,
    label,
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
  autoFocus?: boolean;
  required?: boolean;
  pattern?: string;
  minLength?: number;
  maxLength?: number;
  inputMode?: 'none' | 'text' | 'tel' | 'url' | 'email' | 'numeric' | 'decimal' | 'search';
  readOnly?: boolean;
  spellCheck?: boolean;
  onChange?: (value: string) => void;
  onKeyDown?: KeyboardEventHandler<HTMLElement>;
  onEnter?: () => void;
}

export const SakugaInput = forwardRef<HTMLElement, SakugaInputProps>(function SakugaInput(props, ref) {
  const inner = useRef<HTMLElement | null>(null);
  const {
    type = 'text', label, placeholder, value, defaultValue, name, disabled,
    autoComplete, autoFocus, required, pattern, minLength, maxLength, inputMode,
    readOnly, spellCheck, onChange, onKeyDown, onEnter, className, ...rest
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

  // The upstream field renders its own `<input>` inside the shadow root and
  // only forwards a small attribute set. Validation, focus and editing hints
  // are native-input contract, so mirror them onto that inner element. This
  // wrapper is the adaptation layer; the vendored file is untouched.
  const innerAttrs = {
    type,
    required,
    pattern,
    minlength: minLength,
    maxlength: maxLength,
    inputmode: inputMode,
    readonly: readOnly,
    spellcheck: spellCheck,
    autocomplete: autoComplete,
    'aria-invalid': rest['aria-invalid'],
    'aria-describedby': rest['aria-describedby'],
    'aria-required': required ? 'true' : undefined,
  };
  const innerRef = useRef<HTMLInputElement | null>(null);
  // The upstream field's own `input` event is `composed: false`, so it never
  // leaves its shadow root and the host `onInput` handler below cannot see a
  // real keystroke. (The test helper dispatches a composed event, which is why
  // jsdom page tests never caught this.) Re-publish the inner event as a
  // composed one on the host; a composed original — the test path — is passed
  // through untouched, so onChange still fires exactly once.
  const wiredInput = useRef<HTMLInputElement | null>(null);
  const wiredHandler = useRef<EventListener | null>(null);
  useEffect(() => () => {
    if (wiredInput.current && wiredHandler.current) {
      wiredInput.current.removeEventListener('input', wiredHandler.current);
    }
    wiredInput.current = null;
    wiredHandler.current = null;
  }, []);
  useLayoutEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    let stop: ReturnType<typeof setTimeout> | null = null;
    const apply = () => {
      const host = inner.current;
      const input = host?.shadowRoot?.querySelector<HTMLInputElement>('input');
      if (!input) return false;
      innerRef.current = input;
      const hostElement = inner.current;
      if (hostElement) delegateInnerInput(hostElement);
      if (wiredInput.current !== input) {
        if (wiredInput.current && wiredHandler.current) {
          wiredInput.current.removeEventListener('input', wiredHandler.current);
        }
        const wire = (event: Event) => {
          if (event.composed) return;
          // Re-publish on the HOST, not the inner input: dispatching on the
          // input would run the vendored dust/form listener twice per key.
          // The host getter returns the same value, so the wrapper's handler
          // still reports what the user typed.
          hostElement?.dispatchEvent(
            new Event('input', { bubbles: true, composed: true }),
          );
        };
        input.addEventListener('input', wire);
        wiredInput.current = input;
        wiredHandler.current = wire;
      }
      for (const [key, attr] of Object.entries(innerAttrs)) {
        if (attr === undefined || attr === null || attr === false) input.removeAttribute(key);
        else input.setAttribute(key, attr === true ? '' : String(attr));
      }
      if (type) input.type = type;
      return true;
    };
    if (!apply()) {
      timer = setInterval(() => {
        if (apply() && timer) {
          clearInterval(timer);
          timer = null;
        }
      }, 50);
      stop = setTimeout(() => {
        if (timer) clearInterval(timer);
        timer = null;
      }, 2000);
    }
    if (autoFocus) {
      const focus = () => {
        const input = hostShadowInput(inner.current);
        if (input) input.focus();
        else return false;
        return true;
      };
      if (!focus()) {
        const focusTimer = setInterval(() => {
          if (focus()) clearInterval(focusTimer);
        }, 50);
        setTimeout(() => clearInterval(focusTimer), 2000);
      }
    }
    return () => {
      if (timer) clearInterval(timer);
      if (stop) clearTimeout(stop);
    };
  });

  return createElement('sakuga-input', {
    ...hostProps(rest),
    // `type` and `autocomplete` are read only while rendering the inner
    // `<input>` and are not observed attributes, so they must exist before the
    // element connects; a layout-effect write would leave every email/password
    // field rendered as `type=text`.
    type,
    label,
    placeholder,
    autocomplete: autoComplete,
    ref: (node: HTMLElement | null) => {
      inner.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) (ref as { current: HTMLElement | null }).current = node;
    },
    class: className,
    // A search field is a searchbox, not a plain textbox. The native input
    // gets that role from `type=search`; the host must carry it too because
    // Testing Library and assistive tech query the light-DOM host.
    role: rest.role ?? (type === 'search' ? 'searchbox' : 'textbox'),
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
    key: `${signature}|${label}`,
    // Read once in render(); not an observed attribute.
    label,
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

/* ──────────────────────────────── Loading ───────────────────────────── */

export interface SakugaLoadingProps {
  /** Visible + accessible name. */
  label?: string;
  /** 0..1 when the work can report progress; omit for a busy crawl toward 90%. */
  value?: number;
  doneLabel?: string;
  /** Hide the visible caption (the progress bar keeps its accessible label). */
  showLabel?: boolean;
  className?: string;
  style?: CSSProperties;
  id?: string;
  'data-testid'?: string;
  onDone?: () => void;
}

/**
 * A loading pattern built on the upstream progress bar.
 *
 * Loading screens have no meaningful completion number, so with no `value`
 * the bar eases toward 90% and stops there — the same non-linear crawl the
 * upstream `demo` uses, but owned by Qubit so `demo` is never shipped. Give it
 * a `value` for a real long job and it becomes a determinate bar.
 */
export const SakugaLoading = forwardRef<HTMLElement, SakugaLoadingProps>(function SakugaLoading(props, ref) {
  const {
    label = 'Loading…', value, doneLabel, showLabel = true,
    className, style, id, onDone,
  } = props;
  const controlled = value !== undefined;
  const clamped = controlled ? Math.max(0, Math.min(1, value)) : undefined;
  const [crawl, setCrawl] = useState(clamped ?? 0.08);

  useEffect(() => {
    if (controlled) return;
    setCrawl(0.08);
    const timer = setInterval(() => {
      setCrawl((current) => (current >= 0.9 ? current : Math.min(0.9, current + (0.9 - current) * 0.12)));
    }, 350);
    return () => clearInterval(timer);
  }, [controlled]);

  const current = controlled ? (clamped as number) : crawl;

  return createElement(
    'div',
    {
      id,
      className: ['qsk-loading', className].filter(Boolean).join(' '),
      style,
      role: 'status',
      'aria-live': 'polite',
      'aria-label': label,
      'data-testid': props['data-testid'],
    },
    showLabel ? createElement('div', { className: 'qsk-loading__label' }, label) : null,
    createElement(SakugaProgress, {
      ref,
      label,
      value: current,
      doneLabel,
      onDone,
    }),
  );
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
    // Read once in render(); not an observed attribute.
    label,
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
