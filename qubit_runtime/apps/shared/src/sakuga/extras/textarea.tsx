/* SakugaTextarea — a native <textarea> in the Sakuga frame.
 *
 * Upstream has no multi-line control; this is a real textarea with the
 * documented `--sk-*` palette and the compact chrome, plus the engine's dust
 * (`type`) on input and wedges (`focus`) on focus. Because it is the native
 * element, value/name, required, maxLength and form submission are unchanged.
 */
import {
  createElement,
  forwardRef,
  useId,
  type CSSProperties,
  type ChangeEvent,
  type FocusEvent,
  type ReactNode,
} from 'react';
import {
  ExtraField,
  FIELD_STYLE,
  useFocusFx,
  useTypeFx,
} from './common';

export interface SakugaTextareaProps {
  label: string;
  value?: string;
  defaultValue?: string;
  placeholder?: string;
  rows?: number;
  name?: string;
  required?: boolean;
  disabled?: boolean;
  maxLength?: number;
  autoFocus?: boolean;
  readOnly?: boolean;
  id?: string;
  className?: string;
  style?: CSSProperties;
  description?: ReactNode;
  onChange?: (value: string) => void;
}

export const SakugaTextarea = forwardRef<HTMLTextAreaElement, SakugaTextareaProps>(
  function SakugaTextarea(props, ref) {
    const {
      label, value, defaultValue, placeholder, rows = 3, name, required,
      disabled, maxLength, autoFocus, readOnly, id, className, style,
      description, onChange,
    } = props;
    const autoId = useId();
    const fieldId = id ?? `qsk-textarea-${autoId}`;
    const typeFx = useTypeFx();
    const focusFx = useFocusFx();

    return createElement(
      ExtraField,
      { label, htmlFor: fieldId, description, className, style },
      createElement('textarea', {
        id: fieldId,
        ref,
        className: 'qsk-extra__control qsk-textarea',
        style: {
          ...FIELD_STYLE,
          width: '100%',
          minHeight: rows * 20 + 16,
          resize: 'vertical',
        },
        name,
        placeholder,
        rows,
        required,
        disabled,
        maxLength,
        autoFocus,
        readOnly,
        value,
        defaultValue,
        onChange: (event: ChangeEvent<HTMLTextAreaElement>) => {
          typeFx(event.currentTarget);
          onChange?.(event.currentTarget.value);
        },
        onFocus: (event: FocusEvent<HTMLTextAreaElement>) => focusFx(event.currentTarget),
      }),
    );
  },
);
