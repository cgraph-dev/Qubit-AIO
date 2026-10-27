export type DiffKind = 'same' | 'add' | 'del';

export interface DiffToken {
  kind: DiffKind;
  text: string;
}

export interface DiffResult {
  tokens: DiffToken[];
  added: number;
  removed: number;
  changed: boolean;
}

function tokenize(text: string): string[] {
  return text.match(/\s+|\S+/g) ?? [];
}

/**
 * Word-level diff (whitespace preserved) using an LCS table.
 *
 * Cheap for the bounded preview sizes the helper produces; no dependency and
 * no HTML injection — the renderer creates text nodes only.
 */
export function diffWords(original: string, proposed: string): DiffResult {
  const a = tokenize(original);
  const b = tokenize(proposed);
  const n = a.length;
  const m = b.length;

  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] =
        a[i] === b[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const tokens: DiffToken[] = [];
  let added = 0;
  let removed = 0;
  let i = 0;
  let j = 0;

  const push = (kind: DiffKind, text: string) => {
    if (!text) return;
    const last = tokens[tokens.length - 1];
    if (last && last.kind === kind) last.text += text;
    else tokens.push({ kind, text });
  };

  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push('same', b[j]);
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      push('del', a[i]);
      removed += a[i].trim() ? 1 : 0;
      i += 1;
    } else {
      push('add', b[j]);
      added += b[j].trim() ? 1 : 0;
      j += 1;
    }
  }
  while (i < n) {
    push('del', a[i]);
    removed += a[i].trim() ? 1 : 0;
    i += 1;
  }
  while (j < m) {
    push('add', b[j]);
    added += b[j].trim() ? 1 : 0;
    j += 1;
  }

  return {
    tokens,
    added,
    removed,
    changed: added > 0 || removed > 0,
  };
}
