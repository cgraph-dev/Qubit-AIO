/**
 * Run-turn shape for a single conversation turn.
 *
 * Framework-neutral. Some agent runtimes emit run failures through an error
 * subscription even when the run promise resolves, so a turn only counts as
 * successful when BOTH the execute promise resolves AND no error event fired.
 * Ported from the pinned upstream mobile surface's `conversation-run.ts`,
 * rebranded to Qubit naming.
 */
type RunError = { error: unknown; context?: { agentId?: string } };

export type RunErrorEvent = RunError;

export type RunErrorSubscription = { unsubscribe: () => void };

/**
 * Execute one conversation turn, treating a subscribed run-error as a
 * failure even if `execute` resolved cleanly. Always tears the subscription
 * down before returning/throwing.
 */
export async function runConversationTurn(
  agentId: string,
  execute: () => Promise<unknown>,
  subscribe: (listener: (event: RunErrorEvent) => void) => RunErrorSubscription,
): Promise<void> {
  let failure: Error | undefined;
  const subscription = subscribe((event) => {
    if (event.context?.agentId && event.context.agentId !== agentId) return;
    failure = event.error instanceof Error ? event.error : new Error(String(event.error));
  });
  try {
    await execute();
    if (failure) throw failure;
  } finally {
    subscription.unsubscribe();
  }
}
