import { messages } from '../i18n/runtime.js'

/** Tool name → progress verb, resolved against the active language at call time. */
export const toolVerbs = (): Record<string, string> => messages().content.verbs

export const toolVerb = (name: string): string | undefined => toolVerbs()[name]

// Rotating status-bar verbs. The user watches these while the agent works, so
// they narrate the work in two registers: netrunner (obfuscating, penetrating,
// rootkitting) and gross (puking bytes, shitting out packets). Not the old
// "contemplating / pondering" voice — that read as a customer-service bot.
// Kept short and ASCII: `padVerb` widens the whole list to the longest entry,
// so one long line costs every other verb its room on a narrow status rule.
// Plain words only (no kaomoji) — cleanThinkingText strips this vocabulary out
// of streamed reasoning, and a glyph in the list would eat real text.
export const VERBS = [
  'obfuscating',
  'penetrating',
  'rootkitting',
  'fuzzing the heap',
  'bruteforcing',
  'pivoting laterally',
  'deauth-ing the link',
  'poisoning the cache',
  'hijacking the socket',
  'escalating privilege',
  'exfiltrating quietly',
  'shellshocking',
  'decompiling you',
  'keylogging the daemon',
  'cracking the checksum',
  'tunnelling through',
  'ghosting the tracer',
  'spoofing the handshake',
  'heap-spraying',
  'weaponising the diff',
  'puking bytes',
  'shitting out packets',
  'fermenting a fork',
  'vomiting stack traces',
  'nuking the build',
  'digesting your code',
  'rotting in RAM',
  'secreting glue code',
  'swallowing the panic',
  'hacking up tokens',
  'coughing up errors',
  'mutating in the dark',
  'bleeding edge cases',
  'moulting the lockfile',
  'regurgitating the spec',
  'birthing a bug'
]
