// The approval / clarify card shown above the composer. One card at a time: the
// oldest prompt of the session being viewed, with a count of the ones queued
// behind it. Every piece of text that came from Qubit (the command, the diff,
// the question, the choices) is set with textContent, so none of it is parsed
// as markup.

import {
  buildEditDiff,
  diffRowPrefix,
  encodeMultiSelectAnswer,
  formatCountdown,
  PROMPT_KINDS,
} from './gateway-prompts.mjs';

const APPROVAL_LABEL_KEYS = Object.freeze({
  once: 'browser_control.approve_once',
  session: 'ui.session.f7f1997c',
  always: 'ui.always',
  deny: 'browser_control.reject',
});

function isEditingField(element) {
  const tag = String(element?.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || element?.isContentEditable === true;
}

export function createPromptCard({ container, t, controller, now = () => Date.now() }) {
  const doc = container.ownerDocument;
  // Clarify answers survive a re-render (a status change, a queued request).
  const drafts = new Map();
  let renderedKey = '';
  let renderedSignature = '';
  let countdownNode = null;

  function el(tag, className = '', text = '') {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function button(label, className, onClick, { disabled = false, choice = '' } = {}) {
    const node = el('button', className, label);
    node.type = 'button';
    node.disabled = disabled;
    if (choice) node.dataset.choice = choice;
    node.addEventListener('click', onClick);
    return node;
  }

  function draftFor(entry) {
    let draft = drafts.get(entry.key);
    if (!draft) {
      draft = { text: {}, picks: {} };
      drafts.set(entry.key, draft);
    }
    return draft;
  }

  function header(kickerKey, titleKey) {
    const wrap = el('div', 'agent-prompt-head');
    wrap.append(el('span', 'agent-prompt-kicker', t(kickerKey)), el('strong', 'agent-prompt-title', t(titleKey)));
    return wrap;
  }

  function diffBlock(edit) {
    const details = el('details', 'agent-prompt-edit');
    details.open = true;
    const diff = buildEditDiff(edit.oldText, edit.newText);
    const summary = el('summary', '', `${t('exec_approval.edit_title')}${edit.path ? `: ${edit.path}` : ''}`);
    const pre = el('pre', 'agent-prompt-diff');
    for (const row of diff.rows) {
      const line = el('span', `agent-prompt-diff-row is-${row.kind}`);
      line.textContent = `${diffRowPrefix(row.kind)}${row.text}`;
      pre.append(line);
    }
    if (!diff.rows.length) pre.append(el('span', 'agent-prompt-diff-row is-gap', edit.path || ''));
    details.append(summary, pre);
    if (diff.truncated || edit.truncated) details.append(el('small', 'agent-prompt-note', t('exec_approval.edit_truncated')));
    return details;
  }

  function buildApproval(entry) {
    const frag = doc.createDocumentFragment();
    frag.append(header('exec_approval.kicker', 'exec_approval.title'));
    if (entry.edit) frag.append(diffBlock(entry.edit));
    else frag.append(el('code', 'agent-prompt-command', entry.command));
    if (entry.commandTruncated && !entry.edit) frag.append(el('span', 'agent-prompt-note', t('exec_approval.command_truncated')));
    if (entry.description) frag.append(el('span', 'agent-prompt-reason', entry.description));
    frag.append(el('span', 'agent-prompt-hint', t('exec_approval.hint')));
    return frag;
  }

  function approvalActions(entry) {
    const actions = el('div', 'agent-prompt-actions');
    const busy = entry.status === 'answering';
    for (const choice of entry.choices) {
      const node = button(
        t(APPROVAL_LABEL_KEYS[choice]),
        choice === 'deny' ? 'secondary agent-prompt-deny' : 'agent-prompt-allow',
        () => { void controller.answerApproval(entry.key, choice); },
        { disabled: busy, choice },
      );
      actions.append(node);
    }
    return actions;
  }

  function answersFor(entry, draft) {
    const answers = {};
    for (const question of entry.questions) {
      const typed = String(draft.text[question.qid] || '').trim();
      const picks = [...(draft.picks[question.qid] || [])];
      if (question.multiSelect) {
        const labels = typed ? [...picks, typed] : picks;
        answers[question.qid] = labels.length ? encodeMultiSelectAnswer(labels) : null;
      } else {
        answers[question.qid] = typed || picks[0] || null;
      }
    }
    return answers;
  }

  function hasAnyAnswer(answers) {
    return Object.values(answers).some((value) => typeof value === 'string' && value !== '');
  }

  function buildClarify(entry) {
    const draft = draftFor(entry);
    const frag = doc.createDocumentFragment();
    frag.append(header('clarify.kicker', 'clarify.title'));
    const busy = entry.status === 'answering';
    const interactive = entry.actionable && !busy;
    const single = entry.questions.length === 1 && !entry.questions[0].multiSelect;
    const submit = () => {
      const answers = answersFor(entry, draft);
      if (!hasAnyAnswer(answers)) return;
      void controller.answerClarify(entry.key, answers);
    };
    for (const question of entry.questions) {
      const block = el('div', 'agent-prompt-question');
      block.append(el('p', 'agent-prompt-question-text', question.question));
      const picks = new Set(draft.picks[question.qid] || []);
      if (question.choices.length) {
        const list = el('div', 'agent-prompt-choices');
        for (const label of question.choices) {
          const choiceNode = button(label, `secondary agent-prompt-choice${picks.has(label) ? ' is-selected' : ''}`, () => {
            if (question.multiSelect) {
              if (picks.has(label)) picks.delete(label); else picks.add(label);
            } else {
              picks.clear();
              picks.add(label);
              draft.text[question.qid] = '';
            }
            draft.picks[question.qid] = new Set(picks);
            if (single && interactive) {
              submit();
              return;
            }
            choiceNode.classList.toggle('is-selected', picks.has(label));
            choiceNode.setAttribute('aria-pressed', String(picks.has(label)));
            if (!question.multiSelect) {
              for (const sibling of list.children) {
                if (sibling !== choiceNode) {
                  sibling.classList.remove('is-selected');
                  sibling.setAttribute('aria-pressed', 'false');
                }
              }
            }
          }, { disabled: !interactive });
          choiceNode.setAttribute('aria-pressed', String(picks.has(label)));
          list.append(choiceNode);
        }
        block.append(list);
        if (question.multiSelect) block.append(el('small', 'agent-prompt-note', t('clarify.multi_hint')));
      }
      const input = el('input', 'agent-prompt-input');
      input.type = 'text';
      input.maxLength = 4000;
      input.placeholder = t('clarify.placeholder');
      input.setAttribute('aria-label', question.question);
      input.value = draft.text[question.qid] || '';
      input.disabled = !interactive;
      input.addEventListener('input', () => {
        draft.text[question.qid] = input.value;
        if (input.value && !question.multiSelect) draft.picks[question.qid] = new Set();
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.isComposing) {
          event.preventDefault();
          submit();
        }
      });
      block.append(input);
      frag.append(block);
    }
    return { frag, submit, draft };
  }

  function buildSignature(entry, snapshot) {
    return JSON.stringify([
      entry.key, entry.status, entry.error, entry.actionable,
      Math.max(0, snapshot.visible.length - 1),
    ]);
  }

  function rebuild(entry, snapshot, firstForKey) {
    const body = el('div', 'agent-prompt-body');
    container.dataset.kind = entry.kind;
    container.dataset.status = entry.status;
    countdownNode = null;
    let focusTarget = null;
    if (entry.kind === PROMPT_KINDS.approval) {
      body.append(buildApproval(entry));
    } else {
      const clarify = buildClarify(entry);
      body.append(clarify.frag);
      if (entry.actionable) {
        const actions = el('div', 'agent-prompt-actions');
        const busy = entry.status === 'answering';
        actions.append(
          button(t('clarify.send'), 'agent-prompt-allow', () => clarify.submit(), { disabled: busy }),
          button(t('ui.skip'), 'secondary', () => {
            const skipped = {};
            for (const question of entry.questions) skipped[question.qid] = null;
            void controller.answerClarify(entry.key, skipped);
          }, { disabled: busy }),
        );
        body.append(actions);
        focusTarget = body.querySelector('.agent-prompt-input');
      }
    }
    if (entry.hasTimeout) {
      countdownNode = el('span', 'agent-prompt-countdown');
      body.append(countdownNode);
    }
    if (entry.kind === PROMPT_KINDS.approval) {
      if (entry.actionable) {
        body.append(approvalActions(entry));
        if (entry.choices.includes('session') || entry.choices.includes('always')) {
          body.append(el('small', 'agent-prompt-note', t('exec_approval.choice_help')));
        }
        // Deny is the default: a stray Enter or Space refuses instead of allowing.
        focusTarget = body.querySelector('[data-choice="deny"]');
      }
    }
    if (!entry.actionable) {
      body.append(el('span', 'agent-prompt-note', t('exec_approval.info_only')));
      body.append(button(t('ui.close'), 'secondary', () => controller.dismiss(entry.key)));
    }
    if (entry.error) {
      const failure = el('span', 'agent-prompt-error', t(
        entry.kind === PROMPT_KINDS.clarify ? 'clarify.send_failed' : 'exec_approval.send_failed',
        { error: entry.error },
      ));
      failure.setAttribute('role', 'alert');
      body.append(failure);
    }
    const queued = Math.max(0, snapshot.visible.length - 1);
    if (queued) body.append(el('span', 'agent-prompt-queue', t('exec_approval.queue_more', { count: queued })));
    container.replaceChildren(body);
    if (firstForKey && focusTarget && !isEditingField(doc.activeElement)) {
      try {
        focusTarget.focus({ preventScroll: true });
      } catch {
        // Focus is a convenience; the card is usable without it.
      }
    }
  }

  function updateCountdown(entry, current) {
    if (!countdownNode) return;
    countdownNode.textContent = t('exec_approval.countdown', { time: formatCountdown(entry.deadline - current) });
  }

  function render(snapshot) {
    const entry = snapshot?.visible?.[0];
    if (!entry) {
      if (renderedKey) {
        container.replaceChildren();
        renderedKey = '';
        renderedSignature = '';
        countdownNode = null;
      }
      container.hidden = true;
      return;
    }
    // Show the card before building it: a browser will not move focus into an
    // element that is still hidden, so Deny would never take the default focus.
    container.hidden = false;
    const signature = buildSignature(entry, snapshot);
    if (signature !== renderedSignature) {
      rebuild(entry, snapshot, entry.key !== renderedKey);
      renderedKey = entry.key;
      renderedSignature = signature;
    }
    updateCountdown(entry, snapshot.now ?? now());
  }

  const unsubscribe = controller.subscribe(render);
  return {
    render,
    destroy() {
      unsubscribe();
      container.replaceChildren();
      container.hidden = true;
    },
  };
}
