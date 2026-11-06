/**
 * Per-panel request bookkeeping for `/qubit-workbench`.
 *
 * Each panel owns one `FetchGuard`, created by `useFetchGuard()`. A guard is a
 * plain mutable object (created once, never rebuilt on render) holding:
 *
 * - `generation` — bumped by every `start()` and by `retire()`;
 * - `token` — the identity of the newest unanswered attempt;
 * - `result` — what the newest attempt produced.
 *
 * `start(fn)` launches one request and keeps the answer ONLY if no newer
 * attempt began and nobody retired the guard meanwhile. `retire()` runs from
 * the effect cleanup, so an answer that lands after the dependency changed (or
 * after unmount) never reaches React state.
 *
 * Why a mutable object and not a `useRef`: the React Compiler rewrites hook
 * bodies in this repo, and a ref read whose value it cannot see changing gets
 * pinned at first render — which silently turns every later "is this still
 * current?" check into a constant. Comparing explicit tokens by identity
 * sidesteps that memoisation.
 */

import { useState } from "react";

export interface FetchGuard<T> {
  generation: number;
  token: object | null;
  result: T | null;
}

/** Create (once) the guard a panel uses to settle one kind of read. */
export function useFetchGuard<T>(): FetchGuard<T> {
  const [cell] = useState<FetchGuard<T>>(() => ({
    generation: 0,
    token: null,
    result: null,
  }));
  return cell;
}

/** Mark the guard's current attempt as superseded (call from effect cleanup). */
export function retire<T>(guard: FetchGuard<T>): void {
  guard.generation += 1;
  guard.token = null;
}

/**
 * Run `load` as the guard's newest attempt and hand the answer back only while
 * it is still current. `settle` receives `undefined` when the answer arrived
 * too late, so callers can leave their previous state untouched.
 */
export function startFetch<T>(
  guard: FetchGuard<T>,
  load: () => Promise<T>,
  settle: (next: T | undefined) => void,
): void {
  guard.generation += 1;
  const token = {};
  guard.token = token;
  void load().then((next) => {
    if (guard.token !== token) return;
    guard.token = null;
    guard.result = next;
    settle(next);
  });
}
