import { describe, expect, it } from "vitest";

import {
  CHAT_ROUTE_PATHS,
  isChatPath,
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
});
