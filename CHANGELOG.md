# Changelog

## Unreleased

## 0.8.0 - 2026-09-30

Native Bonsai and reusable long context bring fast local follow-up questions to
long meetings. This release also adds streamed Markdown answers, Ollama discovery,
hardware-aware audio/LLM setup, Linux CUDA repair and long-recording fixes.

Watch the [performance demo](https://www.youtube.com/watch?v=pDUrVM5XTZw): three
short follow-up answers on an RTX 3070 (8 GB VRAM, 32 GB system RAM), with
1.62–1.83 s to first text after the initial 75.68 s cold request. See
[measurement details](https://github.com/estebanstifli/Meet2Notes/blob/v0.8.0/docs/performance-demo.md)
for conditions and limitations.

### Changes

- Add the narrated Bonsai performance demo and measured cold-load/follow-up
  timings to the README, with a consolidated overview of changes since 0.7.0
  and documentation of the recording methodology and cache limits.

- Play transcript and speaker timestamps against the normalized WAV to avoid
  inaccurate browser seeking in long variable-bitrate MP3 recordings.

- Add automatic audio installation profiles shared by Windows, Linux, Pinokio and
  Settings: CPU INT8 bundles or NVIDIA Small/Turbo with Sherpa CPU diarization.
  Preserve existing choices; install files before applying settings, without inference.
- Add opt-in coordinated model residency, deferred unloads and a pre-output CPU
  retry for Whisper CUDA failures. Keep summary KV state across assistant questions.
- Show elapsed activity and indeterminate progress during long diarize CPU jobs;
  support cancellation while waiting for the isolated worker's response.

- Reuse native Bonsai's complete-transcript KV prefix from AI notes in subsequent
  assistant questions. Show model-loading and context-processing progress before
  the first answer token.
- Render long transcripts in cooperative batches with loading progress and cancel
  stale renders when switching versions. Defer the hidden speaker panel until its
  tab is opened and skip layout work for off-screen transcript rows.

- Apply Bonsai automatic context sizing inside the shared summary adapter, covering
  queued AI notes and speaker notes as well as chat. Check the actual tokenizer
  before generation, without changing saved context preferences.
- Fix hierarchical summary boundary overflows by budgeting the same transcript
  and evidence-group headers that are sent to the model.

- Native Bonsai chat now keeps a stable complete-document prefix and explicitly
  reuses the resident KV cache, with Q4 K/V compression and hybrid-state checkpoints.
  Prompt timing logs report reused/new tokens without logging meeting content.
- Add automatic native Bonsai context growth up to 262,144 total tokens. Preserve
  complete attachments, retain larger loaded windows, and retry GPU allocation
  failures with a host KV cache only when system RAM has sufficient headroom.
  Manual context remains available; no transcript is silently truncated.

- Automatically attach the active completed transcript when selecting a meeting
  in Meeting Assistant or Prompt. The user can remove it; switching scopes clears
  old attachments and ignores stale document requests.

- Prevent native Bonsai factual chat from suppressing names already present in the
  evidence by using neutral repetition sampling for prompt answers. Context and
  output limits remain unchanged; this fixes premature normal-stop answers.
- Show readable meeting source links for attached documents and retrieved excerpts
  in Meeting Assistant and Prompt, including streamed answers. Unknown citation
  labels are marked unverified instead of being linked to unrelated evidence.

- Fix intermittent empty Bonsai answers on follow-up questions: explicitly disable
  thinking in each native chat request instead of relying on a zero reasoning budget.
  Keep the output allowance for the visible answer and report empty native responses
  with safe diagnostics that do not log internal reasoning text.

- Automatically recommend and install Bonsai 27B 1-bit for compatible NVIDIA
  GPUs with 8+ GB VRAM, Ternary for 16+ GB, and LFM for modest/unsupported systems.
  Windows, Linux/macOS, Pinokio and CLI share the policy; updates preserve explicit
  model choices and custom storage locations. Explicit profile/skip options remain available.
- Add pinned, SHA-256-verified Bonsai GGUFs and a private Prism runtime, including
  private CUDA libraries on Windows/Linux and an optional Apple Silicon Metal runtime.
  Native answers support streaming and managed process cleanup without requiring Ollama.
- Make local LLM installation download-only, including Settings installation actions.
  No LLM is loaded and no response is generated as an installation test.
  Automatically selected profiles start with preloading disabled.

- Send Meeting Assistant conversation history as actual user/assistant turns,
  keep the current question last with its meeting evidence, and use a chat-specific
  default instead of the AI notes summarization instruction. Preserve custom prompts
  and bounded history without orphan assistant turns.

- Fix AI notes staying queued when summary generation and search indexing finish
  in the same job update. Process every completed job, retry failed UI refreshes,
  and preserve completed notes when a late generation response still says queued.

- Added configurable streaming for Meeting Assistant and direct Live Assistant
  questions, with incremental Markdown, activity indicators, cancellation, and
  buffered fallback for providers that explicitly reject streaming. Supports local
  llama.cpp models, Ollama and remote providers through LiteLLM.

- Render Markdown in Live Assistant and Meeting Assistant answers, including lists,
  tables and code, with escaped HTML, safe links and the original text for copying.

- Added an Ollama profile in AI Engine with service/executable discovery, a selector
  of verified text-generation models, editable server address, cloud model labels,
  and connection diagnostics. Discovery never loads or downloads a model.
- Send the configured context window to Ollama, preserve explicit model selection,
  and avoid forwarding shared API credentials to the dedicated Ollama profile.

- Automatically discover and activate Linux CUDA 12 / cuDNN 9 libraries for
  Faster Whisper, including libraries installed by pip or conda outside the
  system linker path. Check native library initialization in an isolated process.
- Use CPU/int8 for Auto when Linux CUDA cannot be initialized; show actionable
  diagnostics and a private-environment repair button in Transcription settings.
  Explicit CUDA requests fail early with the repair instructions.
- Install missing Linux CUDA libraries during CUDA setup and Pinokio install/update,
  without changing system drivers or requiring a system-wide CUDA toolkit.

### Updating to 0.8.0

- **Windows launcher:** stop Meet2Notes, then run `update.bat` or rerun
  `install-update.bat`. The stable-Release updater creates a database backup,
  validates dependencies and verifies the saved managed LLM's files.
- **Pinokio:** stop Meet2Notes, select **Update**, then **Start Meet2Notes**.
  Update pulls the current branch, refreshes the private runtime, checks Linux
  CUDA libraries and verifies the saved managed LLM without loading it.
- **macOS/Linux source installation:** stop the app, pull the updated source and
  rerun `./install.sh` with the backend/storage options used for your installation.
- Existing meetings, recordings, settings and downloaded models are retained.
  Updating does not silently switch existing model selections to Bonsai or Turbo.
  Choose a new LLM explicitly in **Settings → AI engine**, or use **Install and
  use recommended profile** in **Settings → Transcription** for the audio bundle.
- A compatible 8–15 GB NVIDIA GPU defaults to Bonsai 1-bit on a fresh install;
  16+ GB defaults to Ternary. System RAM, driver and architecture checks also apply.
  CPU/unsupported systems retain LFM. Installation downloads files without inference.
- Restart after updating the backend or repairing CUDA libraries. Keep a backup
  of important data before manual updates. See
  [safe updates](https://github.com/estebanstifli/Meet2Notes/blob/v0.8.0/docs/updating.md).

## 0.7.0 - 2026-09-29

- Added a paginated meeting library with combined title/description and local
  FTS5 transcript search, tag filters, timestamped excerpts, and direct links
  that highlight the matching segment without starting audio automatically.
- Added persistent meeting tags with assignment, removal, global rename, and
  deletion; migration 013 preserves all existing recordings and transcripts.
- Added reviewable Meeting Assistant quick actions, locally saved custom prompts,
  action editing/deletion, answer copying, and history reset on scope changes.
- Removed the general alpha label and reconciled the roadmap with shipped features.
- Added a six-second README demo with corrected skin-tone colors, captured from
  the real application and a demo recording.

### Updating to 0.7.0

- **Pinokio:** stop Meet2Notes, select **Update**, then **Start Meet2Notes**.
  Update pulls the repository's current branch and refreshes its private runtime.
- **Windows launcher:** rerun `install-update.bat` or use `update.bat` to detect
  this stable GitHub Release and apply the backed-up update.
- Migration 013 runs automatically on startup. Existing meetings, recordings,
  settings and downloaded models are retained. Back up important data before
  updating manually.

## 0.6.2 - 2026-09-28

- Added the optional Anna integration source, Windows Executa build tooling,
  setup instructions, reviewer guide, and acceptance checks to the public repository.
- Anna integration v0.1.3 provides read-only meeting search, timestamped transcripts,
  and existing AI notes through the existing local MCP gateway; its bundled
  Executa uses the independent version v0.1.2.
- Added an English Anna UI with formatted notes, speaker-grouped transcripts,
  readable dates, and paginated content. The Anna app is pending Marketplace review.
- Added adapter tests for protocol registration, argument validation, read-only
  boundaries, disconnected status, and pinned note pagination.
- Kept Anna optional: no new desktop dependencies, startup services, database
  migrations, or changes to recording, transcription, diarization, or note generation.


## 0.6.1 - 2026-09-10

- Added simultaneous microphone and system-audio capture so video calls can
  record both sides of the conversation into one private local meeting.
- Added a clock-aligned 48 kHz mono mixer with per-input resampling, equal-gain
  headroom, gap preservation, and the same mixed frames for live transcription
  and the final WAV recording.
- Redesigned the New transcription source picker with independent microphone
  and system selections, persistent device choices, platform guidance, source
  availability checks, and separate live level meters.
- Preserved the single-source capture API while adding a bounded `source_ids`
  request for exactly one microphone/interface plus one system source.
- Improved capture failure handling for disconnected devices, silent Windows
  loopback outputs, pause/resume, partial stream startup, and cleanup on retry.
- Added cross-platform combined-capture and mixer tests plus detailed Windows,
  macOS, and Linux setup and troubleshooting documentation.

## 0.6.0 - 2026-08-24

- Added a local read-only MCP server for Claude Desktop, ChatGPT Desktop,
  Codex, and compatible `stdio` clients, turning the private meeting archive
  into a directly queryable AI knowledge source.
- Added bounded MCP tools for meeting discovery, metadata, transcript pages,
  completed AI notes, exact FTS5 keyword search, and existing hybrid RAG
  evidence with meeting, speaker, timestamp, and source provenance.
- Added generated Claude Desktop JSON and Codex/ChatGPT Desktop TOML
  configuration in Settings, an explicit MCP access switch, local endpoint
  discovery, loopback enforcement, and a read-only security boundary.
- Added the post-meeting Meeting Assistant with meeting/all-history scope,
  hybrid dense and BM25 retrieval, raw transcript and AI-note attachments,
  context budgeting, and the dedicated assistant avatar.
- Added the configurable Live AI Assistant behavior modes, priority direct
  questions, deterministic question/trigger detection, conversation context,
  and the redesigned movable, resizable, and minimizable widget.
- Added safe stable updates from official GitHub Releases, including user
  confirmation, SQLite backups, migration dry-runs, clean fast-forward checks,
  dependency validation, rollback, and preservation of existing settings and
  data directories.
- Added compact rebuild, copy, Markdown edit, and save controls to meeting AI
  Notes, including Note Format selection, version preservation, manual-edit
  provenance, and navigation protection for unsaved changes.
- Added automatic hierarchical AI notes for transcripts that exceed the model
  context, including block estimation, grounded evidence extraction, recursive
  consolidation, and a final Note Format pass.
- Added confirmation, persistent background progress, and per-meeting logging
  when rebuilding the historical RAG index.
- Defined the independent community-plugin workflow, added a public JSON
  listing and issue template, and coordinated the user, contributor, developer,
  and roadmap documentation around that single flow.

## 0.5.0 - 2026-08-13

- Replaced implicit final post-processing callbacks with an observable pipeline
  for final ASR, diarization, saved-voice matching, filters, and AI analysis.
- Added Plugin API v1 with typed artifacts, actions, filters, deterministic
  priorities, timeouts, failure policies, and Python entry-point discovery.
- Added a lazy shared provider registry for plugin transcription, diarization,
  summary, and embedding engines, model-only extensions, scoped directories,
  declarative settings, and hot registry refresh.
- Added composite transcription results with speaker turns so end-to-end models
  can bypass the separate diarization stage safely.
- Added local plugin management in Settings and a privacy-preserving execution
  ledger containing timings, statuses, and content digests.
- Added a built-in analysis-cleanup filter, a community plugin example, and
  contribution and roadmap documentation.
- Kept the existing Live transcription path and settings unchanged.

## 0.4.0 - 2026-07-26

- Renamed the application and all public entry points to Meet2Notes.
- Added one-command Windows, macOS, and Linux installation with platform-aware
  llama.cpp acceleration and safe CPU fallback.
- Added `meet2notes-models` for automatic download and verification of Faster
  Whisper, sherpa-onnx, and LFM2.5 models.
- Preserved existing LocalMeet2Resume data directories to avoid breaking stored
  recording paths during the rename.
- Rebuilt the README as a product-quality installation, privacy, architecture,
  and platform guide.
- Added a resident sherpa-onnx speaker-diarization worker with model management,
  configurable clustering, and transcript speaker assignment.
- Added a resident llama.cpp summary worker and the official LFM2.5 1.2B
  Q4_K_M managed preset.
- Added independent install, load, unload, status, memory, runtime, and
  generation controls to Settings.
- Added cancellable diarization and summary jobs plus summary persistence.

## 0.3.0 - 2026-07-25

- Rebuilt the transcription workspace as a single minimal editor.
- Added editable, persistent transcription names with automatic numbering.
- Added native audio-source discovery and live capture controls.
- Added Windows WASAPI loopback through PyAudioWPatch.
- Added portable PortAudio adapters for CoreAudio and PipeWire/Pulse/ALSA inputs.
- Added pause, resume, stop, level metering, and automatic final-quality refinement.
- Added true real-time transcription with overlapping low-latency audio windows.
- Added provisional live segments and an automatic full-quality refinement after Stop.
- Added an extensible transcription-engine settings card with runtime-aware device
  and compute-type choices, 100 Whisper languages, decoding controls, and live
  chunk/overlap tuning.
- Isolated Faster Whisper in a dedicated executor and kept the configured model
  resident in memory between transcriptions by default.

## 0.2.0 - 2026-07-25

- Added FFmpeg audio normalization for transcription.
- Added an optional, lazy Faster Whisper adapter with CPU `int8` and CUDA `float16`.
- Added fast, balanced, accurate, and very accurate model profiles.
- Added explicit confirmation before any Whisper model download.
- Added persistent transcription versions and progressive timestamped segments.
- Added segment editing, activation, find and replace, and retranscription.
- Added secure local media playback and a responsive transcript editor.

## 0.1.0 - 2026-07-24

- Added the FastAPI application and local responsive web interface.
- Added versioned SQLite migrations and repositories.
- Added safe media storage, FFmpeg capability detection, and media probing.
- Added a persistent asynchronous job queue with progress and cancellation.
- Added meeting, recording, settings, job, capability, and event APIs.
- Added automated tests and cross-platform continuous integration.
