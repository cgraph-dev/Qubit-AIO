// Friendly model names for the chip, session rows, and headers. Ported from the
// Qubit mobile app's ModelNames.displayParts (core/model/ModelNames.kt) so the
// two clients read the same. The provider prefix, dashes, and date pins are
// dropped; the context window becomes a quiet tag; variant tags are kept so
// distinct ids never collapse.

const DATE_PIN = /-\d{8}$/;
const DATE_SUFFIX = /-(\d{8}|\d{4}-\d{2}-\d{2})$/;
const CONTEXT_TAG = /\[(\d+[mk])]$/i;
const VARIANT_TAGS = [
  [/-fast$/i, 'Fast'],
  [/-flash$/i, 'Flash'],
  [/-thinking$/i, 'Thinking'],
  [/-preview$/i, 'Preview'],
  [/-latest$/i, 'Latest'],
];
const QUANT = /-(?:UD-)?(Q\d(?:_[A-Z0-9]+)*|IQ\d(?:_[A-Z0-9]+)*|F16|BF16)$/i;
const INSTRUCT_MARKER = /-(?:Instruct|Chat)(?:-\d{4})?$/i;
const VENDOR_CASING = [
  [/\bDeepseek\b/g, 'DeepSeek'],
  [/\bGlm\b/g, 'GLM'],
  [/\bMinimax\b/g, 'MiniMax'],
  [/\bOpenai\b/g, 'OpenAI'],
  [/\bErnie\b/g, 'ERNIE'],
  [/\bMimo\b/g, 'MiMo'],
  [/\bBge\b/g, 'BGE'],
  [/\bVl\b/g, 'VL'],
  [/\bIt\b/g, 'IT'],
  [/\bFp8\b/g, 'FP8'],
  [/\bAi\b/g, 'AI'],
];
const PARAMETER_COUNT = /\b(a?)(\d+(?:\.\d+)?)b\b/gi;

function baseModelId(id) {
  const text = String(id || '').trim();
  const slash = text.lastIndexOf('/');
  return slash >= 0 ? text.slice(slash + 1) : text;
}

function titleCaseWords(text) {
  return text.replace(/\b\w/g, (ch) => ch.toUpperCase()).trim();
}

function applyVendorCasing(text) {
  let cased = text.replace(PARAMETER_COUNT, (_, a, n) => `${a.toUpperCase()}${n}B`);
  for (const [pattern, replacement] of VENDOR_CASING) cased = cased.replace(pattern, replacement);
  return cased;
}

function prettifyModelBase(base) {
  if (/^claude-/i.test(base)) {
    // Anthropic ids spell the version with hyphens (haiku-4-5); the name is dotted.
    const rest = base.replace(/^claude-/i, '').replace(/(\d)-(?=\d)/g, '$1.').replace(/-/g, ' ');
    return applyVendorCasing(titleCaseWords(rest));
  }
  if (/^gpt-/i.test(base)) {
    return applyVendorCasing(titleCaseWords(`GPT ${base.replace(/^gpt-/i, '').replace(/-/g, ' ')}`));
  }
  if (/^o\d/i.test(base)) {
    return `o${applyVendorCasing(titleCaseWords(base.slice(1).replace(/-/g, ' ')))}`;
  }
  if (/^gemini-/i.test(base)) {
    return applyVendorCasing(titleCaseWords(`Gemini ${base.replace(/^gemini-/i, '')}`.replace(/-/g, ' ')));
  }
  return applyVendorCasing(titleCaseWords(base.replace(/-/g, ' ')));
}

// Returns { name, tag } so a row can show the tag quieter than the name.
export function friendlyModelParts(id) {
  let base = baseModelId(id);
  const tags = [];
  for (const [pattern, label] of VARIANT_TAGS) {
    if (pattern.test(base)) {
      tags.push(label);
      base = base.replace(pattern, '');
      break;
    }
  }
  const quant = base.match(QUANT);
  if (quant) {
    tags.push(quant[1].split('_')[0].toUpperCase());
    base = base.slice(0, quant.index);
    base = base.replace(INSTRUCT_MARKER, '');
  }
  const context = base.match(CONTEXT_TAG);
  if (context) {
    tags.push(context[1].toUpperCase());
    base = base.slice(0, context.index);
  }
  base = base.replace(DATE_PIN, '').replace(DATE_SUFFIX, '');
  const name = prettifyModelBase(base) || String(id || '').trim() || 'No model';
  return { name, tag: tags.join(' ') };
}

export function friendlyModelName(id) {
  const { name, tag } = friendlyModelParts(id);
  return tag ? `${name} ${tag}` : name;
}
