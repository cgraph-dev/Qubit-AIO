/* SakugaFile — a Sakuga button trigger for the native file input.
 *
 * The hidden `<input type="file">` carries `name` and the real FileList, so a
 * form submits exactly what the browser picked. The visible trigger and the
 * removable chips are Qubit chrome; picking fires `impact` and removing fires
 * `pop`. Removing a file also rebuilds the native FileList where the platform
 * supports `DataTransfer`.
 */
import {
  createElement,
  forwardRef,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from 'react';
import { SakugaButton } from '../elements';
import { emitFxAt } from '../fx';
import {
  DESCRIPTION_STYLE,
  ExtraField,
  HIDDEN_INPUT_STYLE,
  ROW_STYLE,
} from './common';

export interface SakugaFileProps {
  label: string;
  accept?: string;
  multiple?: boolean;
  onFiles?: (files: File[]) => void;
  name?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  style?: CSSProperties;
  description?: ReactNode;
  /** Visible trigger text. Defaults to "Choose file(s)". */
  buttonLabel?: string;
}

const CHIP_STYLE: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  maxWidth: '100%',
  padding: '2px 4px 2px 9px',
  border: '1.5px solid var(--sk-ink, #1d1a4a)',
  borderRadius: 999,
  background: 'var(--sk-card, #fff)',
  color: 'var(--sk-ink, #1d1a4a)',
  fontSize: 12.5,
  fontWeight: 600,
};

const CHIP_NAME_STYLE: CSSProperties = {
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  maxWidth: 220,
};

const CHIP_REMOVE_STYLE: CSSProperties = {
  padding: '1px 8px',
  border: 0,
  borderRadius: 999,
  background: 'var(--sk-chip, #f4f3fc)',
  color: 'var(--sk-ink, #1d1a4a)',
  font: 'inherit',
  lineHeight: 1.2,
  cursor: 'pointer',
};

export const SakugaFile = forwardRef<HTMLInputElement, SakugaFileProps>(
  function SakugaFile(props, ref) {
    const {
      label, accept, multiple, onFiles, name, disabled, id, className, style,
      description, buttonLabel,
    } = props;
    const autoId = useId();
    const fieldId = id ?? `qsk-file-${autoId}`;
    const [files, setFiles] = useState<File[]>([]);
    const inputRef = useRef<HTMLInputElement | null>(null);
    const triggerRef = useRef<HTMLElement | null>(null);

    const publish = (next: File[]) => {
      setFiles(next);
      onFiles?.(next);
    };

    const onPick = (event: ChangeEvent<HTMLInputElement>) => {
      const picked = Array.from(event.currentTarget.files ?? []);
      publish(picked);
      void emitFxAt('impact', triggerRef.current, { scale: 0.7 });
    };

    const remove = (index: number, target: Element | null) => {
      const next = files.filter((_, position) => position !== index);
      const input = inputRef.current;
      if (input) {
        try {
          const transfer = new DataTransfer();
          next.forEach((file) => transfer.items.add(file));
          input.files = transfer.files;
        } catch {
          // jsdom and older engines: keep the native FileList as the browser set it.
        }
      }
      publish(next);
      void emitFxAt('pop', target, { scale: 0.6 });
    };

    return createElement(
      ExtraField,
      { label, htmlFor: fieldId, description, className, style },
      createElement(
        'div',
        { style: { ...ROW_STYLE, flexWrap: 'wrap' } },
        createElement(SakugaButton, {
          ref: (node: HTMLElement | null) => {
            triggerRef.current = node;
          },
          variant: 'secondary',
          fx: false,
          type: 'button',
          disabled,
          'aria-label': `${buttonLabel ?? (multiple ? 'Choose files' : 'Choose file')}: ${label}`,
          onClick: () => inputRef.current?.click(),
        }, buttonLabel ?? (multiple ? 'Choose files' : 'Choose file')),
        createElement('input', {
          id: fieldId,
          ref: (node: HTMLInputElement | null) => {
            inputRef.current = node;
            if (typeof ref === 'function') ref(node);
            else if (ref) (ref as { current: HTMLInputElement | null }).current = node;
          },
          type: 'file',
          name,
          accept,
          multiple,
          disabled,
          tabIndex: -1,
          'aria-hidden': 'true',
          style: HIDDEN_INPUT_STYLE,
          onChange: onPick,
        }),
        files.map((file, index) => createElement(
          'span',
          { key: `${file.name}-${index}`, style: CHIP_STYLE, 'data-qsk-file': file.name },
          createElement('span', { style: CHIP_NAME_STYLE, title: file.name }, file.name),
          createElement('button', {
            type: 'button',
            style: CHIP_REMOVE_STYLE,
            'aria-label': `Remove ${file.name}`,
            disabled,
            onClick: (event: ReactMouseEvent<HTMLButtonElement>) => remove(index, event.currentTarget),
          }, '×'),
        )),
      ),
      files.length
        ? createElement(
          'span',
          { style: DESCRIPTION_STYLE, 'aria-live': 'polite' },
          multiple ? `${files.length} file${files.length === 1 ? '' : 's'} selected` : files[0].name,
        )
        : null,
    );
  },
);
