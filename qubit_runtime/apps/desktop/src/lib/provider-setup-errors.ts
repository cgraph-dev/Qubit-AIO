const PROVIDER_SETUP_ERROR_RE =
  /No (?:inference|Qubit|Hermes|LLM) provider(?: is)? configured|no_provider_configured|set an API key|is set in config\.yaml but no (?:API key|credentials)/i

const SESSION_INFO_CREDENTIAL_WARNING_RE = /^No API key configured for provider '[^']*'\. First message will fail\.$/

/** True when the gateway named this failure itself: preferred over reading the sentence. */
export function isProviderSetupErrorCode(code: null | string | undefined): boolean {
  return code === 'provider_not_configured'
}

export function isProviderSetupErrorMessage(message: null | string | undefined): boolean {
  const text = message?.trim()

  if (!text) {
    return false
  }

  return PROVIDER_SETUP_ERROR_RE.test(text) || SESSION_INFO_CREDENTIAL_WARNING_RE.test(text)
}
