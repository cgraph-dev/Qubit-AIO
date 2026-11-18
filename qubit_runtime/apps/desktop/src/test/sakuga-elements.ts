/* Minimal jsdom stand-ins for the vendored Sakuga custom elements.
 *
 * The real components are upstream scripts from `public/sakuga-vfx/` and are
 * only evaluated by a browser. Vitest does not fetch and run those scripts, so
 * without a stand-in every `sakuga-*` element stays un-upgraded and any test
 * that reaches for the documented inner native control (the supported path is
 * `@qubit/shared/sakuga`'s `typeIntoSakuga`/`clickSakuga` helpers) would fail.
 *
 * These doubles intentionally model only the contract the shared wrappers rely
 * on and document:
 *   - an open shadow root containing the real native control,
 *   - host attributes mirrored onto that control,
 *   - a bubbling `change` emitted from the host when the control flips.
 * They draw nothing, and they never run in production. A test that needs the
 * upstream look belongs in a real browser, not here.
 */

type Ctor = CustomElementConstructor

function define(tag: string, ctor: Ctor): void {
  if (typeof customElements === 'undefined') return
  if (!customElements.get(tag)) customElements.define(tag, ctor)
}

function boolAttr(el: Element, name: string): boolean {
  return el.hasAttribute(name)
}

class SakugaBaseDouble extends HTMLElement {
  // The shared wrapper writes `disabled` as a property once the element is
  // defined, and mirrors `aria-checked` onto the host. Keep both readable as
  // DOM properties so tests can assert the control's real state.
  get disabled(): boolean {
    return this.hasAttribute('disabled')
  }

  set disabled(next: boolean) {
    if (next) this.setAttribute('disabled', '')
    else this.removeAttribute('disabled')
  }

  get ariaChecked(): string | null {
    return this.getAttribute('aria-checked')
  }
}

class SakugaButtonDouble extends SakugaBaseDouble {
  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('button')) return
    const button = document.createElement('button')
    button.type = (this.getAttribute('type') as 'button' | 'submit' | 'reset') || 'button'
    button.disabled = boolAttr(this, 'disabled')
    const slot = document.createElement('slot')
    button.append(slot)
    this.shadowRoot?.append(button)
  }
}

class SakugaToggleDouble extends SakugaBaseDouble {
  get checked(): boolean {
    return this.hasAttribute('checked')
  }

  set checked(next: boolean) {
    if (next) this.setAttribute('checked', '')
    else this.removeAttribute('checked')
    const input = this.shadowRoot?.querySelector('input')
    if (input) input.checked = next
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('input')) return
    const input = document.createElement('input')
    input.type = 'checkbox'
    input.checked = this.checked
    input.disabled = boolAttr(this, 'disabled')
    input.addEventListener('change', () => {
      this.checked = input.checked
      this.dispatchEvent(new Event('change', { bubbles: true }))
    })
    this.addEventListener('click', (event) => {
      // A click that landed inside the shadow already ran the input's own
      // `change`; only a direct host click needs the fallback toggle.
      if (event.composedPath()[0] !== this) return
      if (boolAttr(this, 'disabled')) return
      input.checked = !input.checked
      this.checked = input.checked
      this.dispatchEvent(new Event('change', { bubbles: true }))
    })
    this.shadowRoot?.append(input)
  }
}

class SakugaCheckboxDouble extends SakugaToggleDouble {}

class SakugaInputDouble extends SakugaBaseDouble {
  get value(): string {
    return this.shadowRoot?.querySelector('input')?.value ?? this.getAttribute('value') ?? ''
  }

  set value(next: string) {
    const input = this.shadowRoot?.querySelector('input')
    if (input) input.value = next
    this.setAttribute('value', next)
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('input')) return
    const input = document.createElement('input')
    input.type = this.getAttribute('type') || 'text'
    input.placeholder = this.getAttribute('placeholder') ?? ''
    input.value = this.getAttribute('value') ?? ''
    input.disabled = boolAttr(this, 'disabled')
    input.addEventListener('input', () => {
      this.setAttribute('value', input.value)
    })
    this.shadowRoot?.append(input)
  }
}

class SakugaSliderDouble extends SakugaBaseDouble {
  get valueAsNumber(): number {
    return Number(this.shadowRoot?.querySelector('input')?.value ?? this.getAttribute('value') ?? 0)
  }

  get value(): string {
    return String(this.valueAsNumber)
  }

  set value(next: number | string) {
    const input = this.shadowRoot?.querySelector('input')
    if (input) input.value = String(next)
    this.setAttribute('value', String(next))
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('input')) return
    const input = document.createElement('input')
    input.type = 'range'
    input.min = this.getAttribute('min') ?? '0'
    input.max = this.getAttribute('max') ?? '100'
    input.step = this.getAttribute('step') ?? '1'
    input.value = this.getAttribute('value') ?? '50'
    input.addEventListener('change', () => {
      this.setAttribute('value', input.value)
      this.dispatchEvent(new Event('change', { bubbles: true }))
    })
    this.shadowRoot?.append(input)
  }
}

class SakugaRadioDouble extends SakugaBaseDouble {
  get value(): string {
    const checked = this.shadowRoot?.querySelector<HTMLInputElement>('input:checked')
    return checked?.value ?? this.getAttribute('value') ?? ''
  }

  set value(next: string) {
    this.setAttribute('value', next)
    const inputs = this.shadowRoot?.querySelectorAll<HTMLInputElement>('input')
    inputs?.forEach((input) => {
      input.checked = input.value === next
    })
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('input')) return
    const group = document.createElement('div')
    const selected = this.getAttribute('value')
    for (const option of Array.from(this.querySelectorAll('option'))) {
      const input = document.createElement('input')
      input.type = 'radio'
      input.value = option.getAttribute('value') ?? ''
      input.checked = input.value === selected
      input.disabled = boolAttr(option, 'disabled')
      input.addEventListener('change', () => {
        this.setAttribute('value', input.value)
        this.dispatchEvent(new CustomEvent('change', { bubbles: true, composed: true, detail: { value: input.value } }))
      })
      group.append(input)
    }
    this.shadowRoot?.append(group)
  }
}

class SakugaTabsDouble extends SakugaBaseDouble {
  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('button')) return
    const group = document.createElement('div')
    const selected = Number(this.getAttribute('selected') ?? '0')
    Array.from(this.querySelectorAll('option')).forEach((option, index) => {
      const button = document.createElement('button')
      button.type = 'button'
      button.textContent = option.textContent ?? ''
      button.setAttribute('aria-selected', index === selected ? 'true' : 'false')
      button.addEventListener('click', () => {
        this.setAttribute('selected', String(index))
        this.dispatchEvent(
          new CustomEvent('change', {
            bubbles: true,
            composed: true,
            detail: { index, value: option.getAttribute('value') ?? '', label: option.textContent ?? '' }
          })
        )
      })
      group.append(button)
    })
    this.shadowRoot?.append(group)
  }
}

class SakugaProgressDouble extends SakugaBaseDouble {
  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
    if (this.shadowRoot?.querySelector('div')) return
    this.shadowRoot?.append(document.createElement('div'))
  }
}

class SakugaRatingDouble extends SakugaToggleDouble {}
class SakugaTooltipDouble extends SakugaBaseDouble {
  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' })
  }
}
class SakugaToastDouble extends SakugaBaseDouble {
  show(_text?: string): void {}
}

/**
 * A no-op engine so `loadSakugaCore()` resolves in jsdom instead of waiting
 * forever on a `<script>` the environment never fetches. The fx bus only calls
 * these primitives; a no-op keeps the wiring observable through `onFx`.
 */
function installEngineDouble(): void {
  if (typeof window === 'undefined') return
  const noop = () => undefined
  ;(window as unknown as { SakugaFX?: unknown }).SakugaFX ??= {
    calm: false,
    burst: noop,
    puff: noop,
    shards: noop,
    ring: noop,
    slash: noop,
    lines: noop,
    ticks: noop,
    wob: noop,
    kira: noop,
    sfx: noop,
    hollow: () => ({}),
    dotted: () => ({})
  }
}

/** Register every tag once. Safe to call from a setup file repeatedly. */
export function installSakugaTestElements(): void {
  installEngineDouble()
  define('sakuga-button', SakugaButtonDouble as unknown as Ctor)
  define('sakuga-toggle', SakugaToggleDouble as unknown as Ctor)
  define('sakuga-checkbox', SakugaCheckboxDouble as unknown as Ctor)
  define('sakuga-input', SakugaInputDouble as unknown as Ctor)
  define('sakuga-slider', SakugaSliderDouble as unknown as Ctor)
  define('sakuga-radio', SakugaRadioDouble as unknown as Ctor)
  define('sakuga-tabs', SakugaTabsDouble as unknown as Ctor)
  define('sakuga-progress', SakugaProgressDouble as unknown as Ctor)
  define('sakuga-rating', SakugaRatingDouble as unknown as Ctor)
  define('sakuga-tooltip', SakugaTooltipDouble as unknown as Ctor)
  define('sakuga-toast', SakugaToastDouble as unknown as Ctor)
}
