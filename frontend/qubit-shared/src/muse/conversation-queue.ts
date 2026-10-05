/**
 * One conversation run at a time, while the person can keep composing.
 *
 * Framework-neutral: zero React / DOM / React Native imports, so both the
 * React web console and a future React Native app reuse this unchanged.
 * Ported (behaviour-identical) from the pinned upstream mobile surface's
 * `conversation-queue.ts`.
 */
export type QueuedMessage = { id: string; text: string };

export type ConversationQueueSnapshot = {
  pending: readonly QueuedMessage[];
  running: boolean;
  paused: boolean;
};

export class ConversationQueue {
  private state: ConversationQueueSnapshot = { pending: [], running: false, paused: false };
  private listeners = new Set<() => void>();

  /** Stable identity — safe to hand straight to `useSyncExternalStore`. */
  getSnapshot = (): ConversationQueueSnapshot => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private update(patch: Partial<ConversationQueueSnapshot>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  enqueue(message: QueuedMessage) {
    this.update({ pending: [...this.state.pending, message] });
  }

  remove(id: string) {
    this.update({ pending: this.state.pending.filter((message) => message.id !== id) });
  }

  pause() {
    this.update({ paused: true });
  }

  resume() {
    this.update({ paused: false });
  }

  async flush(send: (message: QueuedMessage) => Promise<void>): Promise<void> {
    if (this.state.running || this.state.paused) return;
    this.update({ running: true });
    try {
      while (this.state.pending.length && !this.state.paused) {
        const [message, ...pending] = this.state.pending;
        this.update({ pending });
        await send(message);
      }
    } catch (error) {
      // The failed message is already in the transcript. Never resend it
      // implicitly — pause and rethrow so the caller decides what happens next.
      this.update({ paused: true });
      throw error;
    } finally {
      this.update({ running: false });
    }
  }
}
