import { describe, expect, it } from 'vitest';
import { diffWords } from '@/lib/diff';

describe('diffWords', () => {
  it('marks identical text as unchanged', () => {
    const result = diffWords('hello world', 'hello world');
    expect(result.changed).toBe(false);
    expect(result.added).toBe(0);
    expect(result.removed).toBe(0);
    expect(result.tokens.map((t) => t.text).join('')).toBe('hello world');
  });

  it('counts word replacements as add + delete', () => {
    const result = diffWords('make this fast', 'make this careful');
    expect(result.added).toBe(1);
    expect(result.removed).toBe(1);
    const add = result.tokens.find((t) => t.kind === 'add');
    const del = result.tokens.find((t) => t.kind === 'del');
    expect(add?.text).toBe('careful');
    expect(del?.text).toBe('fast');
  });

  it('preserves whitespace exactly', () => {
    const original = 'one  two\nthree';
    const proposed = 'one  two\nfour';
    const result = diffWords(original, proposed);
    const same = result.tokens
      .filter((t) => t.kind === 'same')
      .map((t) => t.text)
      .join('');
    expect(same).toBe('one  two\n');
  });

  it('handles insertions and deletions at the edges', () => {
    expect(diffWords('', 'hello').added).toBe(1);
    expect(diffWords('hello', '').removed).toBe(1);
    expect(diffWords('', 'hello').tokens).toEqual([{ kind: 'add', text: 'hello' }]);
  });

  it('does not count whitespace-only changes as words', () => {
    const result = diffWords('a b', 'a  b');
    expect(result.added).toBe(0);
    expect(result.removed).toBe(0);
    expect(result.changed).toBe(false);
  });
});
