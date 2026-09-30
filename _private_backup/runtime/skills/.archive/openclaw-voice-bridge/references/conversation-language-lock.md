# Conversation Language Lock — English→Romanian Flip (2026-08-23)

## Symptom
User asks in English `How are you?` (Right Ctrl or dashboard), bridge answers in Romanian `Sunt bine, multumesc.` even with no Romanian in the immediate turn. After Romanian was spoken once, every subsequent English turn bleeds Romanian for 4 turns until `Stop speaking Romanian` is issued.

Reproduced: `How are you?` via `/api/type` with empty history still returned Romanian (21 chars) after the fix attempt that only patched `SYSTEM_PROMPT` — root cause was not the prompt alone.

## Root Cause
`personal-knowledge.md` is injected as `<about-the-user>` context on every turn:
```
- Answer in English or Romanian. Romanian when the conversation is in Romanian.
```
The 3B model (huihui_ai/llama3.2-abliterate:3b) prioritized this context line over the system prompt. With `ConversationState max_turns=4`, Romanian also lingered in `as_prompt()` history. Tested isolated: `How are you?` without personal-knowledge → English; with that file → Romanian.

Also: DeepSeek/Perplexity/Ollama fallback system prompts in `orchestrator.py` had no language rule, so they inherited the bleed when history contained Romanian.

## Fix (3 layers, all required)

1. **personal-knowledge.md** (both `~/.config/openclaw-voice-bridge/personal-knowledge.md` and `config/personal-knowledge.md`):
   ```
   OLD: - Answer in English or Romanian. Romanian when the conversation is in Romanian.
   NEW: - Answer in the SAME language as the current question (English question → English answer, Romanian question → Romanian answer). Default to English.
   ```

2. **voicebridge/local_llm.py** — hardened `SYSTEM_PROMPT` + per-turn directive:
   ```python
   SYSTEM_PROMPT = (
       "CRITICAL LANGUAGE RULE: Reply in the EXACT SAME language as the CURRENT "
       "User message (the last User: line). If English → English, if Romanian → Romanian. "
       "Ignore previous turns' language. Never mix languages. Never respond in Romanian to an English question."
   )
   # In chat_local(), after building user_content:
   _ro_markers = ("ă","â","ș","ş","ț","ţ","î")
   _ro_words = {"salut","buna","bună","ce","faci","cum","esti","ești","sunt","bine","multumesc","mulțumesc",...}
   _is_ro = any(c in _low for c in _ro_markers) or any(w in _low.split() for w in _ro_words)
   _lang_directive = "Reply in Romanian." if _is_ro else "Reply in English. Do NOT reply in Romanian."
   user_content += f"User: {text}\n[{_lang_directive} Current message is {'Romanian' if _is_ro else 'English'} — ignore previous turns.]"
   ```
   Tested: `How are you?` + Romanian history now → `I'm functioning well...` (was `Sunt bine`). `Salut, ce faci?` → Romanian. `Good morning` + Romanian history → English.

3. **voicebridge/orchestrator.py** fallback prompts — added to `_chat_deepseek_stream`, `_chat_deepseek`, `_chat_perplexity`:
   ```
   Reply in the SAME language as the current User message — if it is English, reply in English; if Romanian, reply in Romanian. Ignore previous turns' language.
   ```

## Dashboard Transcription Visibility (same session)

**Second bug:** Activity feed `conversation` cards showed generic `text:"conversation"` + `summary:reply` — user couldn't verify ASR quality (did `How are you?` transcribe correctly?).

**Fix:**
- `orchestrator.py _handle_conversation` (local path):
  ```python
  self.feed_push({"kind":"summary", "text": text[:300], "summary": trim_spoken(reply,300), "route":"local", "detail":"conversation"})
  log_event("conversation.local.summary", text=text[:160], summary=reply[:160])
  ```
- Fallback path (DeepSeek/Perplexity) same:
  ```python
  self.feed_push({"kind":"summary", "text": text[:300], "summary": trim_spoken(reply,300), "route":"fallback", "detail":"conversation"})
  ```
- `webui.py _event_to_feed_item` new mappings:
  ```python
  if evt == "conversation.local.summary":
      return {"kind":"summary", "text": ev.get("text",""), "summary": ev.get("summary",""), "route":"local", "detail":"conversation"}
  if evt == "conversation.fallback.summary": ...
  if evt == "conversation.local": # legacy journal before fix
      return {"kind":"summary", "text": ev.get("text","") or "conversation", "summary": f"({ev.get('chars','?')} chars reply)", ...}
  ```
- `frontend/activity.html` — labeled rendering:
  ```js
  const isConversation = it.detail === 'conversation' && it.summary && it.text && it.text !== 'conversation';
  // text → "You said: How are you?" (blue #72aaff), summary → "Bot: I'm functioning well..." (purple #b48cff)
  ```

## Verification
- `curl -X POST /api/type {"text":"How are you?"}` → activity now shows `text:"How are you?"` + `summary:"Sunt bine"` before fix, `summary:"I'm functioning well..."` after.
- `curl -X POST /api/type {"text":"Hello hello how are you today?"}` → `summary:"I'm functioning well. What do you need?"` with `text` preserved.
- `tail events.jsonl` shows `conversation.local.summary` with both fields for persistence across restarts (load_history path).

## Pitfall
Do not re-add vague `Answer in English or Romanian` wording to personal-knowledge.md — it overrides the 3B's system prompt. Keep the explicit `SAME language as current question` form. When updating knowledge banks for bilingual users, always test with `personal-knowledge` injected.
