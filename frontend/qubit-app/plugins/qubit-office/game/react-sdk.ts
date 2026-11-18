/* The office bundle gets React from the dashboard, never from a second copy.
 *
 * `vite.office-game.config.ts` resolves `react` to this module. The dashboard
 * exposes `window.__QUBIT_PLUGIN_SDK__.React` before loading a plugin, so the
 * shared Sakuga wrappers (createElement/hooks/forwardRef) use the host's exact
 * React runtime. Bundling React instead would split the hook dispatcher.
 */
type ReactRuntime = Record<string, unknown> & {
  createElement: (...args: never[]) => unknown;
};

const SDK = (globalThis as {
  __QUBIT_PLUGIN_SDK__?: { React?: ReactRuntime };
}).__QUBIT_PLUGIN_SDK__;

const React = SDK?.React;

if (!React) {
  throw new Error('QUBIT_PLUGIN_SDK_REACT_MISSING');
}

export const createElement = React.createElement as never;
export const Children = React.Children as never;
export const isValidElement = React.isValidElement as never;
export const forwardRef = React.forwardRef as never;
export const useCallback = React.useCallback as never;
export const useEffect = React.useEffect as never;
export const useId = React.useId as never;
export const useLayoutEffect = React.useLayoutEffect as never;
export const useMemo = React.useMemo as never;
export const useRef = React.useRef as never;
export const useState = React.useState as never;
export const useImperativeHandle = React.useImperativeHandle as never;
export const useContext = React.useContext as never;
export const Fragment = React.Fragment as never;
export default React;
