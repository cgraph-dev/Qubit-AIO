# Qubit dashboard design system

The dashboard is a local operator console for a voice assistant. Its visual language is a deep-night command workspace: blue-black surfaces, warm white text, restrained signal-lime for readiness and primary actions, and a cool cyan secondary signal for navigation and context. The Talk route is a wide three-part workspace rather than a centered form: quick actions on the left, the live command canvas in the middle, and health/next actions on the right.

## Interaction rules

- Talk is the primary action. Typed input is the reliable fallback and is always visible in the first viewport.
- The first viewport communicates three things immediately: Qubit is ready, what the operator can do next, and where a live command will appear.
- Activity, Reports, Settings, and Pair device are shared navigation destinations. In-page tools remain available from Talk without creating duplicate routes.
- The Talk workspace uses a command rail, a central voice stage, a typed-command strip, a live-session panel, and a context panel. These are structural regions, not decorative cards; each has one clear job.
- Buttons use plain-language labels, 44px minimum touch targets, visible focus, and a small press response. Hover is supplementary and never carries meaning alone.
- Motion is limited to page entry, status emphasis, and the soft orbit around the talk stage. It uses transform/opacity where possible and is disabled for reduced-motion users.
- Error, unavailable, and pending states explain the next safe action; they do not imply that a task succeeded.

## Responsive behavior

The shared stylesheet serves desktop and mobile static pages. On desktop the Talk workspace uses a 220px rail, flexible command area, and 292px context panel. Below 1120px the context panel moves below the main workspace; below 760px the rail becomes an icon-first touch bar and the command area remains the primary focus. Controls stack without horizontal scrolling. Existing API contracts, routes, audio behavior, and permission gates are unchanged.
