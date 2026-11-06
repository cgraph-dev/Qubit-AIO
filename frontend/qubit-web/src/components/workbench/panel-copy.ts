/**
 * Copy for each panel's loading / not-mounted / error states.
 *
 * Kept out of the component files (and centralised here) for two reasons: the
 * wording of "backend not available" must be identical everywhere on the page,
 * and a module that exports only components is what Fast Refresh needs.
 */

/** What each panel is waiting on, for the message body. */
export interface PanelCopy {
  /** Short region name, e.g. "Sessions". */
  region: string;
  /** One line on what this region would show once the route exists. */
  purpose: string;
}

/**
 * The copy each panel uses for its loading / not-mounted / error states.
 * Kept together (and out of the component files) so the wording of the three
 * states stays consistent across the page and Fast Refresh still works.
 */
export const PANEL_COPY = {
  sessions: {
    region: "Sessions",
    purpose: "this panel lists the sessions the workbench can open.",
  },
  transcript: {
    region: "Transcript",
    purpose: "this panel shows the selected session's messages and hosts the composer.",
  },
  workspace: {
    region: "Workspace",
    purpose: "this panel browses the files the agent can see.",
  },
  file: {
    region: "File preview",
    purpose: "this panel previews a workspace file.",
  },
  timeline: {
    region: "Timeline",
    purpose: "this panel shows the session's recent turns and activity events.",
  },
} as const satisfies Record<string, PanelCopy>;
