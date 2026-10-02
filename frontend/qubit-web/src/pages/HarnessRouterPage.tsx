/**
 * Harness Router — the Unified Harness Protocol console pattern as a
 * Qubit-owned page (route `/harness-router`; route registration in `App.tsx`
 * is a NEEDS-BRIDGE item — this file only exports the component).
 *
 * Port of the upstream console UX (Apache-2.0; upstream
 * `HarnessRouter/harnessrouter`, reference checkout `/tmp/hr-src` — exact
 * repo+commit+paths are recorded in the feature handoff): harness list
 * across backends, task submit with a per-task harness switch, sessions
 * create/resume/inspect, streaming snapshots, files/artifacts, cancellation,
 * and structured failures; console sections Agent harnesses / Starter Kits /
 * Plugins — rebranded Qubit-only, onto existing Qubit backends (WorkerGuard
 * `agent.session`, the task ledger, the planner tool backends, the
 * `codex-runtime` port, brokered file ops).
 *
 * The upstream reference harness is deliberately NOT rendered or routed here
 * (no row, no path, no fallback). Streaming here is a polling snapshot over
 * the existing `fetchJSON` transport, never a new WS/SSE/CDP client.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelHarnessTask,
  createHarnessSession,
  getHarnessArtifact,
  inspectHarnessSession,
  listHarnessPlugins,
  listHarnessFiles,
  listHarnessTasks,
  listHarnesses,
  listStarterKits,
  normalizeFailure,
  normalizeHarnessFiles,
  normalizeHarnessSession,
  normalizeHarnessTasks,
  normalizeHarnesses,
  resumeHarnessSession,
  streamHarnessSession,
  submitHarnessTask,
  type QubitHarness,
  type QubitHarnessFailure,
  type QubitHarnessFile,
  type QubitHarnessSession,
  type QubitHarnessTask,
} from "@/lib/ocFamilyHarnessApi";
import { refusalOrError } from "@/lib/policy-refusal";

function errText(e: unknown): string {
  if (e instanceof Error && e.message) return e.message;
  return refusalOrError(e, "Request failed");
}

function Section({ title, testid, children }: { title: string; testid: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} data-testid={testid} style={{ marginTop: 24 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600 }}>{title}</h2>
      <div style={{ marginTop: 8 }}>{children}</div>
    </section>
  );
}

function FailureRow({ failure }: { failure: QubitHarnessFailure }) {
  return (
    <div data-testid={`failure-${failure.code}`} style={{ marginTop: 4 }}>
      <code style={{ fontSize: 12 }}>{failure.code}</code>
      <span style={{ fontSize: 12 }}> — {failure.detail}</span>
      <span style={{ fontSize: 12 }}> ({failure.retryable ? "retryable" : "not retryable"})</span>
    </div>
  );
}

export default function HarnessRouterPage() {
  const [harnesses, setHarnesses] = useState<QubitHarness[]>([]);
  const [kits, setKits] = useState<QubitHarness[]>([]);
  const [plugins, setPlugins] = useState<QubitHarness[]>([]);
  const [tasks, setTasks] = useState<QubitHarnessTask[]>([]);
  const [prompt, setPrompt] = useState("");
  const [harness, setHarness] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [session, setSession] = useState<QubitHarnessSession | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [live, setLive] = useState(false);
  const [files, setFiles] = useState<QubitHarnessFile[]>([]);
  const [artifact, setArtifact] = useState("");
  const liveTimer = useRef<number | null>(null);

  const refreshLists = useCallback(async () => {
    try {
      const [h, k, p, t] = await Promise.all([
        listHarnesses(),
        listStarterKits(),
        listHarnessPlugins(),
        listHarnessTasks(),
      ]);
      const hs = normalizeHarnesses(h);
      setHarnesses(hs.filter((r) => r.kind === "agent"));
      setKits(normalizeHarnesses(k));
      setPlugins(normalizeHarnesses(p));
      setTasks(normalizeHarnessTasks(t));
      setHarness((prev) => prev || hs.filter((r) => r.kind === "agent")[0]?.id || "");
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    void refreshLists();
  }, [refreshLists]);

  useEffect(() => {
    if (liveTimer.current !== null) {
      window.clearInterval(liveTimer.current);
      liveTimer.current = null;
    }
    if (!live || !sessionId) return;
    liveTimer.current = window.setInterval(() => {
      void streamHarnessSession(sessionId)
        .then((payload) => {
          const next = normalizeHarnessSession(payload);
          if (next) {
            setSession(next);
            setSessionId(next.id);
          }
        })
        .catch((e: unknown) => setError(errText(e)));
    }, 2000);
    return () => {
      if (liveTimer.current !== null) window.clearInterval(liveTimer.current);
      liveTimer.current = null;
    };
  }, [live, sessionId]);

  async function onSubmit() {
    setNotice("");
    setError("");
    try {
      const created = await submitHarnessTask({ prompt, harness });
      const row = normalizeHarnessTasks({ tasks: [created] })[0];
      setNotice(row?.id ? `Submitted ${row.id} on ${harness}` : `Submitted on ${harness}`);
      setPrompt("");
      await refreshLists();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onCancel(taskId: string) {
    setNotice("");
    setError("");
    try {
      await cancelHarnessTask({ taskId });
      setNotice(`Cancelled ${taskId}`);
      await refreshLists();
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onCreateSession() {
    setNotice("");
    setError("");
    try {
      const created = await createHarnessSession({ harness });
      const next = normalizeHarnessSession(created);
      if (next) {
        setSession(next);
        setSessionId(next.id);
        setNotice(`Session ${next.id} created on ${harness}`);
      }
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onResume() {
    setNotice("");
    setError("");
    try {
      const resumed = await resumeHarnessSession(sessionId);
      const next = normalizeHarnessSession(resumed);
      if (next) {
        setSession(next);
        setSessionId(next.id);
        setNotice(`Session ${next.id} resumed`);
      }
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onInspect() {
    setNotice("");
    setError("");
    try {
      const found = await inspectHarnessSession(sessionId);
      const next = normalizeHarnessSession(found);
      if (next) {
        setSession(next);
        setSessionId(next.id);
      }
      const fl = await listHarnessFiles({ session: sessionId });
      setFiles(normalizeHarnessFiles(fl));
    } catch (e) {
      setError(errText(e));
    }
  }

  async function onArtifact(path: string) {
    setError("");
    try {
      const body = await getHarnessArtifact(path);
      const text =
        typeof body === "string"
          ? body
          : JSON.stringify((body as { result?: unknown }).result ?? body, null, 2);
      setArtifact(text);
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <main aria-label="Harness router" data-testid="harness-router-page" style={{ padding: 16 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>Harness Router</h1>
      <p style={{ fontSize: 13, opacity: 0.8 }}>
        Route each task to the right Qubit harness: agent harnesses across Qubit backends,
        curated starter kits, and catalog plugins — with sessions, live snapshots,
        files, cancellation, and structured failures.
      </p>
      {notice ? <p data-testid="harness-notice" style={{ fontSize: 13 }}>{notice}</p> : null}
      {error ? (
        <p data-testid="harness-error" role="alert" style={{ fontSize: 13 }}>
          {error}
        </p>
      ) : null}

      <Section title="Agent harnesses" testid="section-agent-harnesses">
        {harnesses.length === 0 ? (
          <p style={{ fontSize: 13 }}>No agent harnesses reported.</p>
        ) : (
          <ul aria-label="Agent harness list">
            {harnesses.map((h) => (
              <li key={h.id} data-testid={`harness-${h.id}`} style={{ fontSize: 13 }}>
                <strong>{h.id}</strong>
                {h.backend ? ` · ${h.backend}` : ""}
                {h.state ? ` · ${h.state}` : ""}
                {h.description ? ` — ${h.description}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Submit task" testid="section-submit-task">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <label htmlFor="harness-task-prompt" style={{ fontSize: 13 }}>
            Task
          </label>
          <input
            id="harness-task-prompt"
            data-testid="harness-task-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="What should the harness do?"
            style={{ minWidth: 280 }}
          />
          <label htmlFor="harness-switch" style={{ fontSize: 13 }}>
            Harness
          </label>
          <select
            id="harness-switch"
            data-testid="harness-switch"
            value={harness}
            onChange={(e) => setHarness(e.target.value)}
          >
            {harnesses.map((h) => (
              <option key={h.id} value={h.id}>
                {h.id}
                {h.backend ? ` (${h.backend})` : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            data-testid="harness-submit"
            disabled={!prompt.trim() || !harness}
            onClick={() => void onSubmit()}
          >
            Submit
          </button>
        </div>
      </Section>

      <Section title="Tasks" testid="section-tasks">
        {tasks.length === 0 ? (
          <p style={{ fontSize: 13 }}>No tasks yet.</p>
        ) : (
          <ul aria-label="Harness task list">
            {tasks.map((t) => (
              <li key={t.id} data-testid={`task-${t.id}`} style={{ fontSize: 13 }}>
                <strong>{t.id}</strong>
                {t.harness ? ` · ${t.harness}` : ""}
                {t.state ? ` · ${t.state}` : ""}
                {t.title ? ` — ${t.title}` : ""}
                <button
                  type="button"
                  data-testid={`cancel-${t.id}`}
                  onClick={() => void onCancel(t.id)}
                  style={{ marginLeft: 8 }}
                >
                  Cancel
                </button>
                {t.failure ? <FailureRow failure={t.failure} /> : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Sessions" testid="section-sessions">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button type="button" data-testid="session-create" disabled={!harness} onClick={() => void onCreateSession()}>
            Create session
          </button>
          <label htmlFor="harness-session-id" style={{ fontSize: 13 }}>
            Session
          </label>
          <input
            id="harness-session-id"
            data-testid="harness-session-id"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            placeholder="session id"
          />
          <button type="button" data-testid="session-resume" disabled={!sessionId} onClick={() => void onResume()}>
            Resume
          </button>
          <button type="button" data-testid="session-inspect" disabled={!sessionId} onClick={() => void onInspect()}>
            Inspect
          </button>
          <label style={{ fontSize: 13 }}>
            <input
              type="checkbox"
              data-testid="session-live"
              checked={live}
              onChange={(e) => setLive(e.target.checked)}
            />{" "}
            Follow live
          </label>
        </div>
        {session ? (
          <div data-testid="session-detail" style={{ marginTop: 8, fontSize: 13 }}>
            <div>
              <strong>{session.id}</strong>
              {session.harness ? ` · ${session.harness}` : ""}
              {session.state ? ` · ${session.state}` : ""}
              {session.title ? ` — ${session.title}` : ""}
            </div>
            {session.transcript ? (
              <pre data-testid="session-transcript" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
                {session.transcript}
              </pre>
            ) : null}
            {(() => {
              const direct = session.failure;
              if (direct) return <FailureRow failure={direct} />;
              return null;
            })()}
          </div>
        ) : null}
      </Section>

      <Section title="Files and artifacts" testid="section-files">
        {files.length === 0 ? (
          <p style={{ fontSize: 13 }}>No files listed — inspect a session first.</p>
        ) : (
          <ul aria-label="Session files">
            {files.map((f) => (
              <li key={f.path} style={{ fontSize: 13 }}>
                <button type="button" data-testid={`artifact-${f.path}`} onClick={() => void onArtifact(f.path)}>
                  {f.path}
                </button>
                {f.sizeBytes !== undefined ? ` (${f.sizeBytes} bytes)` : ""}
              </li>
            ))}
          </ul>
        )}
        {artifact ? (
          <pre data-testid="artifact-body" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
            {artifact}
          </pre>
        ) : null}
      </Section>

      <Section title="Starter Kits" testid="section-starter-kits">
        {kits.length === 0 ? (
          <p style={{ fontSize: 13 }}>No starter kits reported.</p>
        ) : (
          <ul aria-label="Starter kit list">
            {kits.map((k) => (
              <li key={k.id} data-testid={`kit-${k.id}`} style={{ fontSize: 13 }}>
                <strong>{k.id}</strong>
                {k.description ? ` — ${k.description}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Plugins" testid="section-plugins">
        {plugins.length === 0 ? (
          <p style={{ fontSize: 13 }}>No harness plugins reported.</p>
        ) : (
          <ul aria-label="Harness plugin list">
            {plugins.map((p) => (
              <li key={p.id} data-testid={`plugin-${p.id}`} style={{ fontSize: 13 }}>
                <strong>{p.id}</strong>
                {p.state ? ` · ${p.state}` : ""}
                {p.description ? ` — ${p.description}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <div data-testid="harness-failure-probe" hidden>
        {normalizeFailure(null) === null ? "failure-probe-ok" : "failure-probe-bad"}
      </div>
    </main>
  );
}
