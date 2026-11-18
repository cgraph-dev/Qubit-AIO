/* SakugaNumber — a native number input in the Sakuga frame with Sakuga steppers.
 *
 * The input is the native `<input type="number">` (so min/max/step and form
 * submission are real); only its spinner is hidden, and two compact
 * `SakugaButton`s step it. onChange reports a number when the field parses and
 * the raw string otherwise (an empty/invalid field), so a caller never has to
 * guess which happened.
 */
import {
  createElement,
  forwardRef,
  useId,
  useRef,
  type CSSProperties,
  type ChangeEvent,
  type FocusEvent,
  type ReactNode,
} from 'react';
import { SakugaButton } from '../elements';
import { emitFxAt } from '../fx';
import {
  clampNumber,
  ExtraField,
  FIELD_STYLE,
  ROW_STYLE,
  useFocusFx,
  useTypeFx,
} from './common';

export interface SakugaNumberProps {
  label: string;
  value?: number | string;
  defaultValue?: number | string;
  min?: number;
  max?: number;
  step?: number;
  name?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  style?: CSSProperties;
  description?: ReactNode;
  onChange?: (value: number | string) => void;
  inputProps?: Record<string, unknown>;
}

export const SakugaNumber = forwardRef<HTMLInputElement, SakugaNumberProps>(
  function SakugaNumber(props, ref) {
    const {
      label, value, defaultValue, min, max, step = 1, name, disabled,
      id, className, style, description, onChange, inputProps,
    } = props;
    const autoId = useId();
    const fieldId = id ?? `qsk-number-${autoId}`;
    const inputRef = useRef<HTMLInputElement | null>(null);
    const controlled = value !== undefined;
    const shown = controlled ? (value === undefined || value === null ? '' : String(value)) : undefined;
    const typeFx = useTypeFx();
    const focusFx = useFocusFx();

    const emit = (raw: string) => {
      if (raw === '') {
        onChange?.('');
        return;
      }
      const parsed = Number(raw);
      onChange?.(Number.isFinite(parsed) ? parsed : raw);
    };

    const setNative = (next: string) => {
      const input = inputRef.current;
      if (input && input.value !== next) input.value = next;
    };

    const stepBy = (direction: 1 | -1) => {
      const input = inputRef.current;
      const base = Number(input?.value ?? shown ?? '');
      const start = Number.isFinite(base) ? base : 0;
      const next = clampNumber(Number((start + direction * step).toFixed(6)), min, max);
      setNative(String(next));
      onChange?.(next);
      void emitFxAt(direction > 0 ? 'pop' : 'tick', input, { scale: 0.5 });
    };

    const nativeValue = inputRef.current?.value ?? shown
      ?? (defaultValue === undefined ? '' : String(defaultValue));
    const nativeNumber = Number(nativeValue);
    const hasNumber = nativeValue !== '' && Number.isFinite(nativeNumber);
    const atMin = typeof min === 'number' && hasNumber && nativeNumber <= min;
    const atMax = typeof max === 'number' && hasNumber && nativeNumber >= max;

    return createElement(
      ExtraField,
      { label, htmlFor: fieldId, description, className, style },
      createElement(
        'div',
        { style: ROW_STYLE },
        createElement(SakugaButton, {
          variant: 'secondary',
          fx: false,
          type: 'button',
          disabled: disabled || atMin,
          'aria-label': 'Decrease',
          className: 'qsk-stepper',
          onClick: () => stepBy(-1),
        }, '−'),
        createElement('input', {
          ...(inputProps ?? {}),
          id: fieldId,
          ref: (node: HTMLInputElement | null) => {
            inputRef.current = node;
            if (typeof ref === 'function') ref(node);
            else if (ref) (ref as { current: HTMLInputElement | null }).current = node;
          },
          className: 'qsk-extra__control qsk-number__input',
          style: { ...FIELD_STYLE, width: 'var(--sk-number-width, 92px)', textAlign: 'center' },
          type: 'number',
          name,
          min,
          max,
          step,
          disabled,
          value: shown,
          defaultValue: controlled ? undefined : defaultValue,
          onChange: (event: ChangeEvent<HTMLInputElement>) => {
            typeFx(event.currentTarget);
            emit(event.currentTarget.value);
          },
          onBlur: (event: FocusEvent<HTMLInputElement>) => {
            const raw = event.currentTarget.value;
            const parsed = Number(raw);
            if (raw === '' || !Number.isFinite(parsed)) return;
            const next = clampNumber(parsed, min, max);
            if (next !== parsed) {
              event.currentTarget.value = String(next);
              onChange?.(next);
            }
          },
          onFocus: (event: FocusEvent<HTMLInputElement>) => focusFx(event.currentTarget),
        }),
        createElement(SakugaButton, {
          variant: 'secondary',
          fx: false,
          type: 'button',
          disabled: disabled || atMax,
          'aria-label': 'Increase',
          className: 'qsk-stepper',
          onClick: () => stepBy(1),
        }, '+'),
      ),
    );
  },
);
