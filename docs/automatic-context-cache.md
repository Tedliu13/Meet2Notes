# Automatic context and prompt caching

AI Engine and Live Assistant have an **Automatic context** setting, enabled by
default. With it enabled, Context tokens is a starting budget, not a universal
8K ceiling. Disable it to retain a fixed application budget. Output tokens
remain a separate generation limit.

Remote API requests send complete selected attachments. The provider validates
its actual context limit; an overflow produces an actionable error rather than
silently cutting an attachment. Recent conversation history remains bounded,
and RAG without full attachments still selects relevant excerpts.

Local contexts grow within the model window. LFM2.5 and the managed small Qwen3
profiles use 32,768 tokens; Bonsai 8B uses 65,536 and Bonsai 27B uses 262,144.
Custom GGUF metadata and Ollama model metadata supply other model limits.
If discovery fails, the configured local window remains the limit. Larger
windows need more RAM/VRAM: a trained context limit is not a memory guarantee.
Native Bonsai retains its GPU-to-host-cache fallback; standard GGUF has a
conservative host-memory preflight. Ollama handles its own allocations.
Local AI notes can still use hierarchical summarization when the text exceeds
the available window; full chat attachments are not silently truncated.

## Cache behavior

The same single attached transcript uses a stable evidence prefix for AI notes
and follow-up questions. Questions and conversation history follow that prefix.
Other attachment combinations can reuse their unchanged prefix within a chat.
Changing the model, transcript, attachment order, or runtime allocation can
invalidate reuse. Live Assistant uses a moving transcript window, so it cannot
reuse an entire static meeting prefix in the same way.

| Runtime | Behavior |
| --- | --- |
| Native Bonsai | Existing `cache_prompt` support; retain the loaded model to reuse KV state. |
| llama-cpp-python | Resident model automatically reuses matching prompt tokens. A larger resident context is retained for smaller follow-ups. |
| Ollama | Sends request-sized `num_ctx`; keeps the model resident for five minutes when enabled. Runtime prefix reuse is not guaranteed by residency alone. |
| OpenAI | Automatic prefix caching, stable hashed routing key; explicit prefix breakpoint only when LiteLLM declares model support. |
| Anthropic | Ephemeral cache breakpoint on the stable system block, default five-minute lifetime. Cache writes have provider-specific charges. |
| Gemini | Implicit caching; no separately billed persistent cache resource is created by the app. |
| Custom API endpoint | Stable prompt structure, without vendor-specific cache parameters that might break compatibility. Server decides reuse. |

This caches prompt processing, never completed answers. Provider minimum prefix
sizes, expiration, routing and load can prevent cache hits. Reported API cache
token usage is logged when returned; absence of metrics does not prove a hit.

## Validation

Automated tests cover remote requests beyond the old 262K application ceiling,
manual limits, local model ceilings, cache annotations, and identical notes/chat
prefixes without sending meeting data to external providers.

A real native Bonsai 27B 1-bit run on the four-minute New product launch
transcript answered the participants and PDF questions correctly. The second
request reused 1,058 prefix tokens, evaluated 215 new prompt tokens, and took
2.43 seconds end-to-end. These timings are one local run, not a provider or
hardware performance guarantee.

Live OpenAI GPT-6 Luna checks answered both questions correctly. The original
947-token stable prefix was below its 1,024-token cache minimum and produced
zero reused tokens. A separate controlled test appended clearly labelled
synthetic, non-meeting records to exceed the minimum: the second question reused
2,459 tokens and completed in 1.78 seconds. This appendix is a test fixture,
not padding added to production requests.

A live Claude Sonnet 5.5 test used the original four-minute transcript without
synthetic padding. The first answer created a 1,615-token cache (4.69 seconds);
the second reused all 1,615 tokens, wrote no new cache tokens and completed in
2.46 seconds. Both participant and PDF answers matched the transcript.

A live Gemini 3.5 Flash-Lite test used the same transcript plus a clearly
labelled synthetic appendix (about 7.4K input tokens). The first request had no
reported cache hit and took 4.39 seconds. The next two requests reported 4,071
and 4,067 reused tokens and took 0.85 seconds each. This confirms partial implicit
cache reuse, not whole-document caching or a guaranteed hit after two requests.
No explicit cache resource was created. API access succeeded for all three
providers; these tests do not query their remaining billing balances.

## References

- [LiteLLM caching](https://docs.litellm.ai/docs/completion/prompt_caching)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Gemini caching](https://ai.google.dev/gemini-api/docs/caching)
- [Ollama context length](https://docs.ollama.com/context-length)
- [Ollama model residency](https://docs.ollama.com/faq)
