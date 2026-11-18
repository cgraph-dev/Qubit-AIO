/* SakugaTextarea — a native <textarea> in the Sakuga frame.
 *
 * Upstream has no multi-line control; this is a real textarea with the
 * documented `--sk-*` palette and the compact chrome, plus the engine's dust
 * (`type`) on input and wedges (`focus`) on focus. Native props (onKeyDown,
 * onPaste, onDrop, aria-*, data-*, autoFocus …) pass through, so a composer
 * can keep Enter-to-send, image paste/drop and its own test ids. Auto-resize
 * is opt-in; the height follows scrollHeight when enabled.
 */
import {
  createElement,
  forwardRef,
  useId,
  useLayoutEffect,
  useRef,
  type CSSProperties,
  type ChangeEvent,
  type FocusEvent,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';
import {
  ExtraField,
  FIELD_STYLE,
  useFocusFx,
  useTypeFx,
} from './common';

export interface SakugaTextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'onChange' | 'value' | 'defaultValue' | 'className' | 'style'> {
  label?: string;
  value?: string;
  defaultValue?: string;
  rows?: number;
  className?: string;
  style?: CSSProperties;
  description?: ReactNode;
  autoResize?: boolean;
  onChange?: (value: string) => void;
}

export const SakugaTextarea = forwardRef<HTMLTextAreaElement, SakugaTextareaProps>(
  function SakugaTextarea(props, ref) {
    const {
      label, value, defaultValue, onChange, className, style, description,
      autoResize = false, rows = 3, onInput, ...rest
    } = props;
    const autoId = useId();
    const fieldId = rest.id ?? `qsk-textarea-${autoId}`;
    const typeFx = useTypeFx();
    const focusFx = useFocusFx();
    const innerRef = useRef<HTMLTextAreaElement | null>(null);

    const resize = () => {
      if (!autoResize) return;
      const element = innerRef.current;
      if (!element) return;
      element.style.height = 'auto';
      element.style.height = `${Math.max(rows * 20 + 16, element.scrollHeight)}px`;
    };

    useLayoutEffect(() => {
      resize();
    }, [autoResize, rows, value, defaultValue]);

    return createElement(
      ExtraField,
      { label, htmlFor: label ? fieldId : undefined, description, className, style },
      createElement('textarea', {
        ...rest,
        id: fieldId,
        ref: (node: HTMLTextAreaElement | null) => {
          innerRef.current = node;
          if (typeof ref === 'function') ref(node);
          else if (ref) (ref as { current: HTMLTextAreaElement | null }).current = node;
        },
        className: 'qsk-extra__control qsk-textarea',
        style: {
          ...FIELD_STYLE,
          width: '100%',
          minHeight: rows * 20 + 16,
          resize: autoResize ? 'none' : 'vertical',
          ...style,
        },
        rows,
        value,
        defaultValue,
        onChange: (event: ChangeEvent<HTMLTextAreaElement>) => {
          typeFx(event.currentTarget);
          onChange?.(event.currentTarget.value);
          resize();
        },
        onInput: (event) => {
          resize();
          onInput?.(event);
        },
        onFocus: (event: FocusEvent<HTMLTextAreaElement>) => {
          focusFx(event.currentTarget);
          rest.onFocus?.(event);
        },
      }),
    );
  },
);
