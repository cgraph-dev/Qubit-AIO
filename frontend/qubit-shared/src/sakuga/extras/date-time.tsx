/* SakugaDate / SakugaTime — native date/time inputs in the Sakuga frame.
 *
 * The native picker stays authoritative (min/max, value/name, form
 * submission). A small Sakuga button calls `showPicker()` where the browser
 * supports it; where it does not, the button is inert and the native field's
 * own picker still works, so there is no broken affordance.
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
  ExtraField,
  FIELD_STYLE,
  ROW_STYLE,
  useFocusFx,
  useTypeFx,
} from './common';

type NativePickerInput = HTMLInputElement & { showPicker?: () => void };

export interface SakugaDateTimeProps {
  label: string;
  value?: string;
  defaultValue?: string;
  min?: string;
  max?: string;
  name?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  style?: CSSProperties;
  description?: ReactNode;
  onChange?: (value: string) => void;
}

interface DateTimeFieldProps extends SakugaDateTimeProps {
  type: 'date' | 'time';
  openLabel: string;
}

const DateTimeField = forwardRef<HTMLInputElement, DateTimeFieldProps>(
  function DateTimeField(props, ref) {
    const {
      label, value, defaultValue, min, max, name, disabled,
      id, className, style, description, onChange, type, openLabel,
    } = props;
    const autoId = useId();
    const fieldId = id ?? `qsk-${type}-${autoId}`;
    const inputRef = useRef<HTMLInputElement | null>(null);
    const typeFx = useTypeFx();
    const focusFx = useFocusFx();

    const openPicker = () => {
      const input = inputRef.current as NativePickerInput | null;
      if (!input || disabled) return;
      try {
        input.showPicker?.();
      } catch {
        // A browser that blocks showPicker keeps the native field usable.
      }
      void emitFxAt('pop', input, { scale: 0.5 });
    };

    return createElement(
      ExtraField,
      { label, htmlFor: fieldId, description, className, style },
      createElement(
        'div',
        { style: ROW_STYLE },
        createElement('input', {
          id: fieldId,
          ref: (node: HTMLInputElement | null) => {
            inputRef.current = node;
            if (typeof ref === 'function') ref(node);
            else if (ref) (ref as { current: HTMLInputElement | null }).current = node;
          },
          className: 'qsk-extra__control qsk-date__input',
          style: { ...FIELD_STYLE, width: 'var(--sk-date-width, 168px)', flex: '0 1 auto', colorScheme: 'light' },
          type,
          name,
          min,
          max,
          disabled,
          value,
          defaultValue,
          onChange: (event: ChangeEvent<HTMLInputElement>) => {
            typeFx(event.currentTarget);
            onChange?.(event.currentTarget.value);
          },
          onFocus: (event: FocusEvent<HTMLInputElement>) => focusFx(event.currentTarget),
        }),
        createElement(SakugaButton, {
          variant: 'secondary',
          fx: false,
          type: 'button',
          disabled,
          'aria-label': openLabel,
          className: 'qsk-stepper',
          onClick: openPicker,
        }, '▾'),
      ),
    );
  },
);

export const SakugaDate = forwardRef<HTMLInputElement, SakugaDateTimeProps>(
  function SakugaDate(props, ref) {
    return createElement(DateTimeField, {
      ...props,
      ref,
      type: 'date',
      openLabel: 'Open date picker',
    });
  },
);

export const SakugaTime = forwardRef<HTMLInputElement, SakugaDateTimeProps>(
  function SakugaTime(props, ref) {
    return createElement(DateTimeField, {
      ...props,
      ref,
      type: 'time',
      openLabel: 'Open time picker',
    });
  },
);
