import {
  historyStamp,
  kindIcon,
  truncate,
} from '@/lib/format';
import type { HistoryEntry } from '@/lib/types';

interface HistoryConsoleProps {
  history: HistoryEntry[];
  onClear: () => void;
}

export function HistoryConsole({ history, onClear }: HistoryConsoleProps) {
  return (
    <div className="history">
      <div className="history__bar">
        <span className="history__count">
          {history.length.toString().padStart(2, '0')} ENTRIES
        </span>
        <button
          type="button"
          className="micro-btn"
          onClick={onClear}
          disabled={history.length === 0}
        >
          CLEAR
        </button>
      </div>

      {history.length === 0 ? (
        <div className="history__empty">
          <p className="history__empty-kicker">CONSOLE IDLE</p>
          <p>
            Dictation, answers, edits and apply receipts are appended here after
            each turn.
          </p>
          <div className="history__skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </div>
      ) : (
        <ol className="history__list" aria-label="Interaction history">
          {history.map((entry) => (
            <li key={entry.id} className={`history__row history__row--${entry.kind}`}>
              <span className="history__meta">
                <span className="history__time">{historyStamp(entry.ts)}</span>
                <span className="history__kind">
                  <span className="history__icon" aria-hidden="true">
                    {kindIcon(entry.kind)}
                  </span>
                  <span className="history__kindname">{entry.kind}</span>
                </span>
              </span>
              <span className="history__heard" title={entry.heard}>
                {truncate(entry.heard || '—', 46)}
              </span>
              <span className="history__result" title={entry.result}>
                {truncate(entry.result || '—', 62)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
