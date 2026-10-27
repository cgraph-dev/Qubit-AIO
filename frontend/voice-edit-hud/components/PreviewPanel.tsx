'use client';

import { useMemo } from 'react';
import { diffWords } from '@/lib/diff';
import {
  charCount,
  elapsedLabel,
  factsDroppedCount,
  truncate,
} from '@/lib/format';
import type { PreviewPayload } from '@/lib/types';

export type PreviewView = 'diff' | 'original' | 'proposed';

interface PreviewPanelProps {
  preview: PreviewPayload | null;
  view: PreviewView;
  onView: (view: PreviewView) => void;
}

function reviewNotes(preview: PreviewPayload): string[] {
  const notes: string[] = [];
  if (preview.injection_suspected) {
    notes.push('Instruction-like text in the selection was treated as data — review before applying.');
  }
  if (preview.clarification_returned) {
    notes.push('The model asked a question — edit or retry before applying.');
  }
  const dropped = factsDroppedCount(preview);
  if (dropped > 0) {
    notes.push(
      `${dropped} identifier${dropped === 1 ? '' : 's'} from the original were not found in the result.`,
    );
  }
  if (preview.clipboard_ok === false) {
    notes.push(
      `Clipboard copy failed${preview.clipboard_code ? ` (${preview.clipboard_code})` : ''} — use Copy to retry.`,
    );
  }
  return notes;
}

export function PreviewPanel({ preview, view, onView }: PreviewPanelProps) {
  const diff = useMemo(
    () =>
      preview
        ? diffWords(preview.original ?? '', preview.proposed ?? '')
        : null,
    [preview],
  );

  if (!preview || !diff) {
    return (
      <div className="preview preview--empty">
        <div className="empty-frame" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </div>
        <p className="empty-kicker">NO PREVIEW IN BUFFER</p>
        <p className="empty-copy">
          A proposed edit appears here after the helper captures and transforms
          a selection. Nothing is applied without an explicit confirmation.
        </p>
        <div className="empty-legend" aria-hidden="true">
          <span>DIFF</span>
          <span>ORIGINAL</span>
          <span>PROPOSED</span>
        </div>
      </div>
    );
  }

  const notes = reviewNotes(preview);
  const original = preview.original ?? '';
  const proposed = preview.proposed ?? '';

  return (
    <div className="preview">
      <div className="preview__bar">
        <div className="preview__tabs" role="tablist" aria-label="Preview view">
          {(['diff', 'original', 'proposed'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={view === tab}
              className={`preview__tab ${view === tab ? 'is-active' : ''}`}
              onClick={() => onView(tab)}
            >
              {tab}
            </button>
          ))}
        </div>
        <div className="preview__stats" aria-label="Diff statistics">
          <span className="stat stat--add">+{diff.added}</span>
          <span className="stat stat--del">−{diff.removed}</span>
          <span className="stat stat--chars">{charCount(preview)} CH</span>
          <span className="stat stat--time">{elapsedLabel(preview)}</span>
        </div>
      </div>

      <div className="preview__body" data-preview-body>
        {view === 'diff' ? (
          <p className="diff" aria-label="Word diff">
            {diff.tokens.map((token, index) => (
              <span
                key={`${index}-${token.kind}`}
                className={`diff__token diff__token--${
                  token.text.trim() ? token.kind : 'same'
                }`}
                data-diff-token
              >
                {token.text}
              </span>
            ))}
          </p>
        ) : (
          <p className="plain">
            {view === 'original' ? original : proposed}
          </p>
        )}
      </div>

      {notes.length > 0 ? (
        <ul className="preview__notes">
          {notes.map((note) => (
            <li key={note} className="note-chip">
              <span className="note-chip__mark" aria-hidden="true">
                !
              </span>
              {truncate(note, 180)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="preview__ok">
          <span aria-hidden="true">✓</span> Diff reviewed — apply writes the
          proposal through the safe paste primitive.
        </p>
      )}
    </div>
  );
}
