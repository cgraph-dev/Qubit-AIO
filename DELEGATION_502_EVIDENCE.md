# Delegation 502 — root cause evidence (commit 5d9448286)

## Symptom
5 subagents dispatched, all ended `HTTP 502: Stream ended before producing a non-ping SSE event`
on the FINAL SUMMARY call after minutes of successful body work.

## Resolved child route (from code, not inference)
config: delegation.provider='' delegation.model='' delegation.fallback_providers=None
qubit_runtime/tools/delegate_tool_config.py:434-439  -> pure-inherit branch (provider/model None)
qubit_cli/fallback_config.py:146-167 scoped_fallback_chain(..., pinned=False) -> inherits PARENT chain
=> child body turns run on inherited parent route cheapvibecode/deepseek-v4.1-flash (works)
=> child fallback chain = the 2 broken OmniRoute entries

## Proven origin of the 502
curl POST http://127.0.0.1:20128/v1/chat/completions, model=cloudflare-playground/auto, 17630-char prompt:
HTTP/1.1 502 Bad Gateway
{"error":{"message":"Stream ended before producing a non-ping SSE event","type":"stream_early_eof","code":"STREAM_EARLY_EOF"},
 "upstream_details":{"error":{"message":"Prompt too long (max 6000 characters)."}}}
Byte-identical to runtime/logs/errors.log. The 502 is RETURNED BY OmniRoute; it is not an SSE-framing bug in Qubit.
Short prompt (say hi) -> HTTP 200 + valid SSE. => upstream 6000-char cap is the trigger.

## Why the SUMMARY specifically dies
Body turns stay on cheapvibecode (works). The final summary request is the one that overflows the
upstream cap once context is large; it fails -> failover, all attempts land on OmniRoute upstreams:
errors.log:51 duckduckgo-web/gpt-5.4-mini -> HTTP 400 ERR_BAD_REQUEST
errors.log:52 cloudflare-playground/auto -> HTTP 502 STREAM_EARLY_EOF
errors.log:644 [subagent-2] API call failed after 3 retries ... provider=omniroute msgs=65 tokens=~21,746
task-0.log:20:54:32 final | end status=completed exit_reason=completed

## Fix
Pin the delegation route so children never fall through to OmniRoute:
  delegation.provider: cheapvibecode
  delegation.model: deepseek-v4.1-flash
  delegation.fallback_providers: []
Pinning makes scoped_fallback_chain(pinned=True) return [] -> no OmniRoute fallback.
