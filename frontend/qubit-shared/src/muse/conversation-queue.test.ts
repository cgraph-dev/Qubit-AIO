import { describe, expect, it, vi } from "vitest";
import { ConversationQueue, type QueuedMessage } from "./conversation-queue";

const message = (id: string): QueuedMessage => ({ id, text: `text-${id}` });

describe("ConversationQueue", () => {
  it("enqueues and exposes pending messages in order", () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    queue.enqueue(message("b"));
    expect(queue.getSnapshot().pending.map((m) => m.id)).toEqual(["a", "b"]);
  });

  it("removes a pending message by id", () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    queue.enqueue(message("b"));
    queue.remove("a");
    expect(queue.getSnapshot().pending.map((m) => m.id)).toEqual(["b"]);
  });

  it("notifies subscribers on change and stops after unsubscribe", () => {
    const queue = new ConversationQueue();
    const listener = vi.fn();
    const unsubscribe = queue.subscribe(listener);
    queue.enqueue(message("a"));
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    queue.enqueue(message("b"));
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("flushes pending messages FIFO through a single send at a time", async () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    queue.enqueue(message("b"));
    queue.enqueue(message("c"));
    const order: string[] = [];
    let inFlight = 0;
    let maxInFlight = 0;
    const send = async (m: QueuedMessage) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      order.push(m.id);
      await Promise.resolve();
      inFlight -= 1;
    };
    await queue.flush(send);
    expect(order).toEqual(["a", "b", "c"]);
    expect(maxInFlight).toBe(1);
    expect(queue.getSnapshot().pending).toEqual([]);
    expect(queue.getSnapshot().running).toBe(false);
  });

  it("does not flush while paused, and resumes on demand", async () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    queue.pause();
    const send = vi.fn(async () => {});
    await queue.flush(send);
    expect(send).not.toHaveBeenCalled();
    expect(queue.getSnapshot().pending.map((m) => m.id)).toEqual(["a"]);

    queue.resume();
    await queue.flush(send);
    expect(send).toHaveBeenCalledTimes(1);
    expect(queue.getSnapshot().pending).toEqual([]);
  });

  it("stops flushing mid-batch when paused between sends", async () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    queue.enqueue(message("b"));
    const seen: string[] = [];
    const send = async (m: QueuedMessage) => {
      seen.push(m.id);
      queue.pause();
    };
    await queue.flush(send);
    expect(seen).toEqual(["a"]);
    expect(queue.getSnapshot().pending.map((m) => m.id)).toEqual(["b"]);
    expect(queue.getSnapshot().paused).toBe(true);
  });

  it("pauses and rethrows when a send fails, without resending the failed message", async () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    queue.enqueue(message("b"));
    const failure = new Error("boom");
    const seen: string[] = [];
    const send = async (m: QueuedMessage) => {
      seen.push(m.id);
      if (m.id === "a") throw failure;
    };
    await expect(queue.flush(send)).rejects.toBe(failure);
    // The failed message left `pending` before send() ran, so it is never resent.
    expect(seen).toEqual(["a"]);
    expect(queue.getSnapshot().pending.map((m) => m.id)).toEqual(["b"]);
    expect(queue.getSnapshot().paused).toBe(true);
    expect(queue.getSnapshot().running).toBe(false);
  });

  it("ignores a concurrent flush while one is already running", async () => {
    const queue = new ConversationQueue();
    queue.enqueue(message("a"));
    let resolveSend: (() => void) | undefined;
    const send = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSend = resolve;
        }),
    );
    const first = queue.flush(send);
    await Promise.resolve();
    await queue.flush(send); // must be a no-op: already running
    expect(send).toHaveBeenCalledTimes(1);
    resolveSend?.();
    await first;
  });
});
