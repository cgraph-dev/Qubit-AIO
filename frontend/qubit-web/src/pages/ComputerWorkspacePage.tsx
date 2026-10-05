/**
 * /computer — the agent computer console surface.
 *
 * Ported from the upstream mobile `computer.tsx` + `computer-workspace.tsx`
 * (MIT, structural port — React + Tailwind, Qubit dark theme, upstream
 * geometry). The heavy lifting lives in
 * `src/components/om-computer-workspace.tsx`.
 *
 * Backend (probed LIVE, real status codes):
 *   GET  /api/qubit/bot/computer-status        200  actuator/console status
 *   GET  /api/qubit/desktop/ops                200  desktop-op catalog
 *   GET  /api/qubit/desktop/presentation       200  presentation state
 *   POST /api/qubit/desktop/{present,pip}      409  staged confirmation
 *   GET  /api/qubit/agent-workspace/list       200  file roster
 *   GET  /api/qubit/agent-workspace/get        200  single-file read
 *
 * The upstream `/api/computer/*` VM/command family is NOT served by Qubit;
 * those controls are rendered as honest, stated gaps, never dead buttons.
 */
import { ComputerWorkspace } from "@/components/om-computer-workspace";

export default function ComputerWorkspacePage() {
  return (
    <main
      className="qb-upstream settings-page settings-page--wide"
      aria-label="Agent computer"
    >
      <section aria-label="Computer toolbar" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Agent computer</h2>
            <p className="settings-section__desc">
              Your agent works here. Inspect the console, desktop ops and the
              workspace files it can read. Step in whenever you need.
            </p>
          </div>
        </div>
      </section>

      <section aria-label="Computer panels" className="settings-section">
        <ComputerWorkspace />
      </section>
    </main>
  );
}
