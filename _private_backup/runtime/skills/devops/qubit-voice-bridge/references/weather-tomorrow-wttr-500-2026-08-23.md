# Weather "tomorrow" — wttr.in 500 + wrong fallback (2026-08-23)

**Symptom (from `events.jsonl`):**
`check the weather in Bucharest tomorrow.` -> `routing.template weather-bucharest` -> `local_script.run` -> `local_script.failed rc=2 error=""` -> `tts.speak "The search failed."` No weather spoken. Two empty ASR turns before it (`asr.transcribe chars 0, peak_rms 0.26-0.37`) were unrelated (short noisy clips).

**Root cause:**
`scripts/weather.py` extracted `city = "Bucharest tomorrow"` via `re.search(r"weather\s+(?:in|for|at|near)?\s*([A-Za-z\s'\-]+)")` then only stripped `today|now|please|thanks|corest`. `https://wttr.in/Bucharest tomorrow?format=j1` 500s. On exception the function returned `""` -> `main()` returned 2 -> orchestrator treated as `local_script.failed` with empty `error`, so the user heard the generic failure.

Also `wttr.in/Bucharest%20tomorrow` is server-side 500 (verified with `urllib.request`).

**Fix (`scripts/weather.py`):**
- Strip `tomorrow|tommorrow|tommorow` in the city regex post-process: `re.sub(r"\b(today|tomorrow|tommorrow|tommorow|now|please|thanks|corest)\b.*", "", city, flags=re.I)`.
- On `HTTP Error 500` return a human fallback instead of `""`: `f"{city}: forecast temporarily unavailable — try again..."` so the bridge speaks something useful.
- In `main()`, retry once with `tomorrow` stripped if the first answer contains the fallback string.

**Verified live:**
`weather in bucharest tommorrow` / `check the weather in Bucharest tomorrow.` now both `rc=0` -> `Bucharest: 27°C (feels 27°C), Clear, wind 6 km/h NE ... Today 25-35°C.` Same via `scripts/web_search.py` weather branch. `93 tests pass`.

**Clean-room note:** weather questions are routed via `config/task-templates.yaml` `weather-bucharest` (`kind: local-script`, `script: weather.py "{question}"`). The template wins over the LLM classifier. Fixing the script fixes voice + dashboard + direct `python weather.py` calls.
