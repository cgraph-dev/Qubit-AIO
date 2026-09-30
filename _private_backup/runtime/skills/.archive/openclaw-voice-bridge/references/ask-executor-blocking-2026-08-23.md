# Config: `ask_executor` was blocking every coding task (2026-08-23)

**Symptom:** Every `coding` task appeared frozen — bridge waited for `ExecutorChoiceState` answer to `"Qubit or Qubit?"` after `_dispatch_coding()`. Logs showed clean `routing.decision kind=coding` but no `openclaw.dispatch`.

**Root cause:** `~/.config/openclaw-voice-bridge/voice.yaml` had `openclaw.ask_executor: true` (meant as temp until bigger GPU). `orchestrator._dispatch_coding()` does `if ask_executor and not _web_mode: executor_choice.request(...); speak("Who should handle this — Qubit or Qubit?"); return`. Web path skips the question (correct for `qubit-dev` dashboard which is Qubit control surface), but mic/RCtrl path blocks.

**Fix:** Live `voice.yaml` `ask_executor: true -> false`. Repo default `voicebridge/config.py` `ask_executor` also `False` so fresh installs aren't blocked. The sticky-routing lane (`route to qubit`/`clear route`) plus explicit `ask qubit` phrasing still works — this just removes the mandatory question before every coding dispatch.

**Verify:** `grep ask_executor ~/.config/openclaw-voice-bridge/voice.yaml` should show `false` after fix. Re-check via `Config.load().get("openclaw","ask_executor")`.
