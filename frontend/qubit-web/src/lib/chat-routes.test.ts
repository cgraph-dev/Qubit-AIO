import { describe, expect, it } from "vitest";

import {
  CHAT_ROUTE_PATHS,
  FULL_HEIGHT_ROUTE_PATHS,
  isChatPath,
  isFullHeightPath,
  isNativeChatPath,
  isTerminalChatPath,
  normalizeRoutePath,
} from "./chat-routes";

describe("chat-routes", () => {
  it("treats both routes as chat paths", () => {
    expect(isChatPath("/chat")).toBe(true);
    expect(isChatPath("/qubit-chat")).toBe(true);
    expect(isChatPath("/chat/")).toBe(true);
    expect(isChatPath("/qubit-chat/")).toBe(true);
  });

  it("splits the terminal route from the native route", () => {
    expect(isTerminalChatPath("/chat")).toBe(true);
    expect(isTerminalChatPath("/qubit-chat")).toBe(false);
    expect(isNativeChatPath("/qubit-chat")).toBe(true);
    expect(isNativeChatPath("/chat")).toBe(false);
  });

  it("rejects non-chat and prefix-collision paths", () => {
    for (const path of [
      "/",
      "/sessions",
      "/qubit-chats",
      "/chat-extra",
      "/chat/history",
      "/qubit-chat-preview",
    ]) {
      expect(isChatPath(path)).toBe(false);
    }
  });

  it("normalizes a trailing slash but keeps the root", () => {
    expect(normalizeRoutePath("/chat/")).toBe("/chat");
    expect(normalizeRoutePath("/")).toBe("/");
  });

  it("exports exactly the two required chat routes", () => {
    expect([...CHAT_ROUTE_PATHS]).toEqual(["/chat", "/qubit-chat"]);
  });

  it("marks chat routes as full-height", () => {
    for (const path of CHAT_ROUTE_PATHS) {
      expect(isFullHeightPath(path)).toBe(true);
    }
  });

  it("marks the full-bleed console pages as full-height", () => {
    // Both own the viewport with a pinned composer; without the flag their
    // `height: 100%` resolves against an auto-height block ancestor and the
    // page grows to its content (measured 104,305px for /webchat).
    expect(isFullHeightPath("/webchat")).toBe(true);
    expect(isFullHeightPath("/qubit-bot")).toBe(true);
    expect(isFullHeightPath("/webchat/")).toBe(true);
  });

  it("does not mark ordinary pages as full-height", () => {
    for (const path of ["/", "/sessions", "/config", "/analytics", "/files"]) {
      expect(isFullHeightPath(path)).toBe(false);
    }
  });

  it("keeps chat and full-height as separate questions", () => {
    // Merging these predicates would drift the chat-route contract: /webchat is
    // full-height but is NOT a chat route.
    expect(isFullHeightPath("/webchat")).toBe(true);
    expect(isChatPath("/webchat")).toBe(false);
    expect(isFullHeightPath("/analytics")).toBe(false);
    expect(isChatPath("/analytics")).toBe(false);
    expect([...FULL_HEIGHT_ROUTE_PATHS]).toEqual([
      "/chat",
      "/qubit-chat",
      "/webchat",
      "/qubit-bot",
    ]);
  });
});
