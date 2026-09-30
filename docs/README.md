# Documentation

The repository README is the user-facing starting point. The documents in this
directory have narrower responsibilities so installation, extension contracts,
and future plans do not become mixed together.

## Current behavior and public contracts

- [Performance demo](performance-demo.md): native Bonsai on an 8 GB GPU,
  measured cold-load and follow-up timings, and the recording methodology.
- [Automatic LLM setup](automatic-llm-setup.md): hardware profiles, native Bonsai,
  automatic context sizing and KV reuse between notes and assistant questions.
- [Automatic audio setup](automatic-audio-setup.md): transcription/diarization
  profiles, model memory management and long-job activity reporting.
- [Ollama](ollama.md): discovery, model selection, context and assistant streaming.
- [Linux CUDA](linux-cuda.md): private-library detection, repair and CPU fallback.
- [Safe updates](updating.md): stable releases, backups and preserved settings.

- [Meeting library](meeting-library.md): transcript search, tags, reusable
  assistant actions, and their privacy and scope boundaries.

- [Audio capture](audio-capture.md): microphone + system recording and setup on
  Windows, Linux, and macOS.
- [Architecture](architecture.md): process boundaries, workers, storage, and the
  provider registry.
- [Plugin API](plugins.md): how users discover, install, enable, and remove
  plugins, plus the stable hook reference.
- [Plugin and provider development](plugin-development.md): package authors,
  provider contracts, model registration, permissions, tests, and listing
  submissions.
- [Privacy](privacy.md): local data, network access, credentials, and threat
  model.
- [Webhooks](webhooks.md): outbound event contract, delivery guarantees,
  signatures, Live agents, security, and rules for future changes.
- [Live AI Assistant](live-ai-assistant.md): native real-time assistant,
  independent worker, settings, persistence, API, and resource limits.
- [Local MCP server](mcp.md): read-only desktop-client integration, lifecycle,
  tools, configuration, and security boundary.

## Product direction and research

- [Roadmap](roadmap.md): completed foundations and planned work.
- [RAG and MCP analysis](rag-and-mcp.md): historical design research behind the
  implemented retrieval and MCP boundaries.
- [AI engine research](ai-engine-research.md): model/runtime evaluation notes;
  not a user guide or stable API.

The root [product specification](../Meet2Notes.md) records the original product
brief. When it differs from the application or current documentation, the
README, source code, and the public contracts above are authoritative.
