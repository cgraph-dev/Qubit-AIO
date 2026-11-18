/* Test helpers for the Sakuga custom elements.
 *
 * The upstream components keep their real native control in an OPEN shadow
 * root. Testing Library does not pierce shadow roots, so a test that used to
 * call `userEvent.type(input)` must reach the inner input explicitly. These
 * helpers are the one supported way to do that; production code never needs
 * them.
 */

function root(host: Element | null): ShadowRoot | null {
  return host && 'shadowRoot' in host ? (host.shadowRoot as ShadowRoot | null) : null;
}

/** The open shadow root, or null for an element that has not upgraded yet. */
export function sakugaShadow(host: Element | null): ShadowRoot | null {
  return root(host);
}

/** Query one inner element by CSS selector. */
export function sakugaInner<T extends Element = HTMLElement>(host: Element | null, selector: string): T | null {
  return root(host)?.querySelector<T>(selector) ?? null;
}

/** All inner elements matching a selector. */
export function sakugaInnerAll<T extends Element = HTMLElement>(host: Element | null, selector: string): T[] {
  return Array.from(root(host)?.querySelectorAll<T>(selector) ?? []);
}

/** Set a text input's value the way a user would (focus, set, input, change). */
export function typeIntoSakuga(host: Element | null, text: string): void {
  const input = sakugaInner<HTMLInputElement>(host, 'input');
  if (!input) throw new Error('SAKUGA_TEST_NO_INNER_INPUT');
  input.focus();
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

/** Click the inner control (button, checkbox, radio row, star, …). */
export function clickSakuga(host: Element | null, selector = 'button, input'): void {
  const target = sakugaInner<HTMLElement>(host, selector);
  if (!target) throw new Error(`SAKUGA_TEST_NO_TARGET ${selector}`);
  target.click();
}

/** Read a control's current value from its inner native input. */
export function sakugaValue(host: Element | null): string | null {
  const input = sakugaInner<HTMLInputElement>(host, 'input');
  return input ? input.value : null;
}

/** Wait until a custom element has upgraded and attached its shadow root. */
export async function waitForSakuga(host: Element | null, timeoutMs = 2000): Promise<void> {
  if (!host) throw new Error('SAKUGA_TEST_NO_HOST');
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (root(host)) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error('SAKUGA_TEST_UPGRADE_TIMEOUT');
}
