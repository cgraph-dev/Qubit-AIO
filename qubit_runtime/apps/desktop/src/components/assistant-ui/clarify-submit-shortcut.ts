import type { KeyboardEvent } from 'react'

/** Use the form's enabled submit button so validation and pending state agree. */
export function handleClarifySubmitShortcut(event: KeyboardEvent<HTMLFormElement>): void {
  if (
    event.key !== 'Enter' ||
    !(event.metaKey || event.ctrlKey) ||
    event.altKey ||
    event.shiftKey ||
    event.nativeEvent.isComposing ||
    event.defaultPrevented
  ) {
    return
  }

  // Capture before a focused choice toggles or a textarea handles plain Enter.
  event.preventDefault()
  event.stopPropagation()

  // Sakuga buttons render a host custom element, so the old `button[type=submit]`
  // selector no longer finds them; the Button primitive marks them instead.
  const submit = event.currentTarget.querySelector<HTMLElement>(
    'button[type="submit"], [data-sakuga-submit]'
  )

  if (!submit || submit.hasAttribute('disabled') || event.repeat) {
    return
  }

  if (submit instanceof HTMLButtonElement) {
    event.currentTarget.requestSubmit(submit)
  } else {
    event.currentTarget.requestSubmit()
  }
}
