'use client';

import type { HudError } from '@/lib/types';

interface NoticeBannerProps {
  error: HudError | null;
  notice: string | null;
  onDismissError: () => void;
  onDismissNotice: () => void;
}

export function NoticeBanner({
  error,
  notice,
  onDismissError,
  onDismissNotice,
}: NoticeBannerProps) {
  if (!error && !notice) return null;

  if (error) {
    return (
      <div className="banner banner--error" role="alert">
        <span className="banner__mark" aria-hidden="true">
          !
        </span>
        <div className="banner__body">
          <span className="banner__kicker">
            VOICE EDIT REFUSED // {error.code}
          </span>
          <span className="banner__detail">
            {error.detail || 'The turn stopped without a detail message.'}
          </span>
        </div>
        <button
          type="button"
          className="micro-btn"
          onClick={onDismissError}
          aria-label="Dismiss the refusal banner"
        >
          DISMISS
        </button>
      </div>
    );
  }

  return (
    <div className="banner banner--notice" role="status">
      <span className="banner__mark banner__mark--ok" aria-hidden="true">
        ✓
      </span>
      <div className="banner__body">
        <span className="banner__detail">{notice}</span>
      </div>
      <button
        type="button"
        className="micro-btn"
        onClick={onDismissNotice}
        aria-label="Dismiss the notice"
      >
        OK
      </button>
    </div>
  );
}
