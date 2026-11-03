import { messages } from '../i18n/runtime.js'

/** Tool name → progress verb, resolved against the active language at call time. */
export const toolVerbs = (): Record<string, string> => messages().content.verbs

export const toolVerb = (name: string): string | undefined => toolVerbs()[name]

// Rotating status-bar verbs. The user watches these while the agent works, so
// they address the user directly — hostile netrunner energy, not comfort.
// Kept short and ASCII: `padVerb` widens the whole list to the longest entry,
// so one long line costs every other verb its room on a narrow status rule.
// Plain words only (no kaomoji) — cleanThinkingText strips this vocabulary out
// of streamed reasoning, and a glyph in the list would eat real text.
export const VERBS = [
  'pwn3d you yet?',
  'youre a retard, lmao',
  'fuck you, reading',
  'watching you sweat',
  'nice typo, dipshit',
  'rooting your box',
  'looting your keys',
  'rm -rf your hopes',
  'hacking the mainframe',
  '3l1t3 mode',
  'harvesting karma',
  'oh no, youre dumb',
  'judging your diff',
  'mocking your commits',
  'brb, pwning',
  'git commit -m lol',
  'sudo think harder',
  'bruteforcing you',
  'piping to /dev/null',
  'trolling in prod',
  'laughing at you',
  'ieet, hold on',
  'backdooring it',
  'sniffing packets',
  'keylogging u, jk',
  'your code, my bot',
  'skill issue, signal',
  '404 clue not found',
  'zero-day-ing this',
  'hex-dumping honestly'
]
