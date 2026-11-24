// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { ProfileChatApprovalBanner } from "./ProfileChatApprovalBanner";

vi.mock("@/lib/api", () => ({
  api: {
    getProfileChatApproval: vi.fn(),
    respondToProfileChatApproval: vi.fn(),
  },
}));

it.each([
  ["Approve once", "once"],
  ["Deny", "deny"],
] as const)("submits %s against the displayed profile, session and request", async (label, choice) => {
  const approval = {
    prompt_id: "request-1",
    execution_generation: 4,
    description: "Fixture action",
    command: "fixture-command <literal>",
    choices: ["once", "deny"] as ("once" | "deny")[],
  };
  vi.mocked(api.getProfileChatApproval).mockResolvedValue({ approval });
  vi.mocked(api.respondToProfileChatApproval).mockResolvedValue({ status: "resolved" });
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => root.render(<ProfileChatApprovalBanner session="draft-1" profile="work" />));
    expect(container.querySelector("code")?.textContent).toBe(approval.command);
    const button = [...container.querySelectorAll("button")].find((node) => node.textContent === label);
    expect(button).toBeDefined();
    await act(async () => button?.click());
    expect(api.respondToProfileChatApproval).toHaveBeenLastCalledWith("draft-1", "work", approval, choice);
    expect(container.textContent).toBe("");
  } finally {
    await act(async () => root.unmount());
  }
});
