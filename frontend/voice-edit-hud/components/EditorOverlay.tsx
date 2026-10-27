'use client';

import { useEffect, useRef, useState } from 'react';
import { canSubmitEdit } from '@/lib/actions';

interface EditorOverlayProps {
  open: boolean;
  initialText: string;
  connected: boolean;
  onClose: () => void;
  onSubmit: (text: string) => void;
}

export function EditorOverlay({
  open,
  initialText,
  connected,
  onClose,
  onSubmit,
}: EditorOverlayProps) {
  const [text, setText] = useState(initialText);
  const areaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setText(initialText);
    const frame = requestAnimationFrame(() => {
      const node = areaRef.current;
      if (!node) return;
      node.focus();
      node.setSelectionRange(node.value.length, node.value.length);
    });
    return () => cancelAnimationFrame(frame);
  }, [open, initialText]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  const submit = () => {
    const value = text.trim();
    if (!canSubmitEdit(value, connected)) return;
    onSubmit(value);
  };

  return (
    <div
      className="editor-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="editor-title"
      >
        <div className="editor__head">
          <span className="editor__index" aria-hidden="true">
            EDIT
          </span>
          <h2 id="editor-title" className="editor__title">
            Manual edit
          </h2>
          <button
            type="button"
            className="micro-btn"
            onClick={onClose}
            aria-label="Close the editor"
          >
            ESC
          </button>
        </div>
        <p className="editor__lead">
          This text replaces the proposal only. Nothing reaches the target app
          until you Apply.
        </p>
        <textarea
          ref={areaRef}
          className="editor__area"
          value={text}
          spellCheck={false}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
              event.preventDefault();
              submit();
            }
          }}
          aria-label="Proposed edit text"
        />
        <div className="editor__foot">
          <span className="editor__count">{text.trim().length} CH</span>
          <span className="editor__hint">
            Ctrl+Enter submit · Esc close · instruction-like text is treated as
            data
          </span>
          <div className="editor__buttons">
            <button
              type="button"
              className="hud-btn hud-btn--ghost"
              onClick={onClose}
            >
              Discard
            </button>
            <button
              type="button"
              className="hud-btn hud-btn--primary"
              onClick={submit}
              disabled={!canSubmitEdit(text, connected)}
            >
              Submit edit
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
