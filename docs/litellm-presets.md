# LiteLLM model presets

Verified on 2026-10-01 with LiteLLM 1.103.1. Both Settings → AI Engine and
Settings → Live Assistant show this catalog when using Custom local / remote
via LiteLLM. Each section retains its own model, endpoint and credentials.

| Provider | Presets |
|---|---|
| OpenAI | GPT-6 Luna, GPT-6.1 Sol, GPT-6 Astra |
| Anthropic | Claude Haiku 4.5, Claude Sonnet 5.5, Claude Opus 5.5 |
| Google AI Studio | Gemini 3.5 Flash-Lite, Gemini 3.8 Flash, Gemini 3.1 Pro (preview) |

This is a compact selection from current provider catalogs, not a measured
cross-provider popularity ranking. Preview availability may change.
Use a key belonging to the selected provider. Presets clear a previously entered
base URL so a cloud model is not accidentally sent to a local endpoint. Custom
restores the manual model and endpoint within the current editing session.
Existing model IDs outside the catalog remain editable as Custom.

Validation checked LiteLLM's provider routing/model metadata and both browser
selectors. It did not send paid API requests or meeting contents to providers;
account access, quotas and real output quality still depend on each provider.

Sources:

- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [Claude model catalog](https://platform.claude.com/docs/en/models/overview)
- [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models)
- [LiteLLM OpenAI](https://docs.litellm.ai/docs/providers/openai),
  [Anthropic](https://docs.litellm.ai/docs/providers/anthropic),
  [Gemini](https://docs.litellm.ai/docs/providers/gemini)
