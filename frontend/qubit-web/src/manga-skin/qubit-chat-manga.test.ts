// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative: string) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");
const css = read("./qubit-chat-manga.css");
const zip = read("./manga-zip-surfaces.css");
const skin = read("./MangaSkin.tsx");
const chat = read("../pages/ChatPage.tsx");
const loader = read("../components/MangaChatLoader.tsx");

describe("native /qubit-chat Manga agent contract", () => {
  it("has a scoped central-page stylesheet loaded after older art", () => {
    expect(skin.indexOf('import "./qubit-chat-manga.css";')).toBeGreaterThan(
      skin.indexOf('import "./chat-inspector.css";'),
    );
    expect(chat).toContain('data-manga-agent=""');
    expect(css).toContain('html[data-qubit-manga="on"] .qchat[data-manga-agent]');
    expect(css).not.toContain("#app-sidebar");
    expect(css).not.toContain(".qchat__side {");
    expect(css).not.toContain("[data-chat-inspector] {");
  });

  it("owns exactly one transcript and composer frame", () => {
    expect(css).toContain('border-image-source:url("/manga-ui/qubit-chat/transcript.svg")');
    expect(css).toContain('border-image-source:url("/manga-ui/qubit-chat/composer.svg")');
    expect(css).toContain("border-image-slice:45 fill");
    expect(css).toContain("border-image-slice:35 fill");
    expect(zip).not.toContain('background-image:url("/manga-ui/zip/chat-composer.svg")');
    expect(zip).not.toContain('.qchat__transcript,.qchat__messages,.qchat__composer');
    expect(chat).not.toContain('data-qm-zip-asset="chat-transcript"');
  });

  it("keeps existing Sakuga Attach, Voice, Send and session actions", () => {
    expect(chat).toContain('buttonLabel="Attach image, PDF or file"');
    expect(chat).toContain("onFiles={(files) =>");
    expect(chat).toContain("onClick={toggleVoice}");
    expect(chat).toContain("onClick={() => send(input)}");
    expect(chat).toContain("onClick={startFresh}");
    expect(chat).toContain("onClick={copyLast}");
    expect(chat).toContain("onClick={summarizeSession}");
    expect(css).not.toContain(".qchat__composer > button {");
  });

  it("uses genuine session data and a single Sakuga loading animation", () => {
    expect(chat).toContain('aria-valuenow={levelProgress}');
    expect(chat).toContain('{String(sessionToolRuns).padStart(2, "0")}');
    expect(chat).toContain('<MangaChatLoader label="Loading conversation…" />');
    expect(loader).toContain("<SakugaLoading label={label} />");
    expect(loader).not.toContain("qubit-chat/atom.svg");
    expect(loader).not.toContain("qchat__manga-indeterminate");
    expect(loader).not.toContain("QubitAtomLoader");
    expect(loader).toContain('size?: "md" | "inline"');
    expect(chat).toContain('<MangaChatLoader size="inline" />');
    expect(chat).not.toContain("QubitAtomLoader");
    expect(css).toContain(".qchat__manga-atom-inline");
  });

  it("keeps voice diagnostics accessible and native responsive inspector layout", () => {
    expect(chat).toContain('<details className="qchat__voice-details">');
    expect(chat).toContain("<summary>Voice details");
    expect(chat).toContain('data-testid="voice-status"');
    expect(css).toContain("flex:0 0 auto;");
    expect(css).not.toContain("overflow:hidden;\n  gap:0.65rem;");
    expect(css).toContain("@media(prefers-reduced-motion:reduce)");
  });

  it("retains all six independent text-free SVG art assets", () => {
    for (const name of ["header", "hud", "transcript", "composer", "atom", "progress"]) {
      const svg = read(`../../public/manga-ui/qubit-chat/${name}.svg`);
      expect(svg).toContain("<svg ");
      expect(svg).toContain("</svg>");
      expect(svg).not.toContain("<text");
    }
  });
});
