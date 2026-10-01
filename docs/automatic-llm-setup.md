# Automatic local LLM setup

Bonsai 8B 1-bit is also available explicitly in Settings or with
`--llm-profile bonsai-8b` (`install.ps1 -LlmProfile bonsai-8b` on Windows).
It starts at 8,192 tokens and supports at most 65,536 including the response.
Its 1.16 GB GGUF uses the same verified native runtime; Ollama is not required.
For 4 GB NVIDIA laptops start at 8K. Automatic selection covers compatible NVIDIA GPUs with 3–7 GB VRAM; see [measured results and limitations](bonsai-8b-validation.md).

Windows `install.ps1` / `install-update.bat`, Linux/macOS `install.sh`, Pinokio,
and `meet2notes-models` use the same recommendation code. It runs `nvidia-smi`
and reads total RAM; it does not import a model or benchmark inference.

| Hardware for a fresh installation | Selected LLM | Initial context |
|---|---|---:|
| Compatible NVIDIA GPU, 3–7 GB VRAM | Bonsai 8B Q1_0 (1-bit) | 8,192 tokens |
| Compatible NVIDIA GPU, 8–15 GB VRAM | Bonsai 27B Q1_0 (1-bit) | 8,192 tokens |
| Compatible NVIDIA GPU, 16+ GB VRAM | Ternary Bonsai 27B PQ2_0 | 16,384 tokens |
| CPU, smaller GPU, unsupported/unknown configuration | LFM2.5 1.2B Q4 | 16,384 tokens |

Automatic Bonsai selection requires Windows/Linux x86-64, at least 16 GB system
RAM, NVIDIA compute capability 7.5–9.0, and a driver from the 552+ Windows or 550+
Linux series. Small VRAM reservations are allowed (7,800 / 15,800 MiB thresholds).
The largest compatible **single GPU** is selected; multiple cards are not added
together. System RAM is not counted as VRAM. AMD, Intel and Apple unified memory
currently keep the lightweight automatic default. Apple Silicon users can explicitly
install Bonsai with its Metal runtime; this is not an automatic recommendation yet.
Newer GPU architectures also keep LFM until a compatible runtime is validated;
the bundled CUDA 12.4 runtime is not assumed to support them automatically.

These are conservative starting profiles, not a guarantee of available memory:
other applications, transcription and a larger context consume RAM/VRAM too.
32 GB RAM is preferable for Ternary. Actual speed and answer quality are left for
the user to evaluate. Reduce context or GPU layers, unload competing models, or
select LFM if memory is constrained.

## What installation does

- Downloads the selected GGUF and its runtime. Bonsai uses the official Prism
  repositories, pinned revisions and SHA-256 hashes; no Ollama installation is needed.
- Verifies downloads in bounded memory and publishes complete files atomically.
  Runtime archives are unpacked in a staging directory with path validation.
- Installs private CUDA 12.4 libraries alongside the Windows/Linux runtime.
  Linux cuBLAS and CUDA runtime wheels are pinned and hash-verified, extracted
  privately, and passed only to the child process. System CUDA, drivers, Python
  packages and an existing Ollama service are not modified.
- Saves the new profile only after successful installation. Automatic Bonsai
  download failure is reported and falls back to LFM; explicit Bonsai requests
  report the error instead of silently choosing another model.
- Never loads an LLM, allocates its context, or generates a test response.
  Settings **Install** also only downloads. **Load**, or the first AI request,
  starts inference; new automatic profiles have startup preloading disabled.

Full installation also downloads the independent Live Assistant's local model
(LFM by default, an additional 731 MB), without changing or enabling that assistant.
This avoids allocating a second 27B model for live questions. A saved external
Live Assistant provider is left alone; `--models summary` manages only the main LLM.

Allow roughly 8 GB free disk for 1-bit or 12 GB for Ternary, including runtime
extraction overhead. Existing verified GGUFs are reused. Interrupted downloads
are removed and can be retried. The Bonsai profiles share their pinned runtime.
Removing a model in Settings removes its GGUF and releases it from memory; the
shared runtime remains for the other profile and future reinstalls.

The private server binds to localhost on a temporary port with a random API key,
supports streamed responses, and is stopped on unload, replacement or normal app
shutdown. It uses one inference slot and one GPU to keep memory use predictable.
Startup failures produce guidance in the application rather than terminating it.
Native Bonsai requests explicitly disable thinking in the chat template so the
configured output limit is available for the visible answer, including follow-up
questions. A zero server-side reasoning budget alone is insufficient for this model.

## Existing installations and overrides

An existing AI selection is preserved in automatic mode, including Ollama, hosted
APIs and custom GGUFs. Existing managed models are verified/repaired without replacing
their settings. Stored model directories are respected. A user changing the selection
during a download takes precedence over the installer's recommendation.

Pinokio **Update** uses the same policy. The stable Windows updater repairs only
the saved managed model, with no settings writes or database migrations. Legacy
installations without a saved AI profile retain their implicit default during that
transaction. An explicit `--llm-profile` replaces the chosen profile after download.

```powershell
.\install.ps1 -LlmProfile auto
.\install.ps1 -LlmProfile light -AiBackend cpu
.\install.ps1 -LlmProfile bonsai-ternary
.\install.ps1 -LlmProfile none
.\.venv\Scripts\meet2notes-models.exe --models summary --llm-profile bonsai-1bit
```

```bash
./install.sh --llm-profile auto
./install.sh --llm-profile light --ai-backend cpu
./install.sh --llm-profile bonsai-ternary
./install.sh --llm-profile none
meet2notes-models --models summary --llm-profile bonsai-1bit
```

`none` skips only LLM installation. Existing `-Models none` / `--no-models` options
skip all model downloads. Forced CPU installation cannot explicitly select Bonsai;
use the lightweight profile or configure a native model manually in Settings.

## Factual chat sampling

Native Bonsai meeting chat uses a neutral repetition penalty (1.0): repeated names
and numbers from the supplied evidence must remain available in the answer. Other
generation tasks retain the configured penalty. This is independent of the context
window and output allowance. Raising either limit does not fix a short response
that ends normally; a larger output allowance also leaves less room for evidence
inside the same context window.

## Full transcripts and KV reuse

Native Bonsai uses `cache_prompt=true`, a stable document prefix ahead of chat
history, Q4_0 K/V caches, flash attention, and recurrent-state checkpoints in the
pinned Prism runtime. Cache state lives in the running private model process;
unloading/restarting it clears the cache. Editing evidence changes its prefix and
forces re-evaluation. Other jobs or meetings can replace the single slot's cache.
New questions and generated answers still require computation.

Normal AI notes and assistant questions with the same complete transcription
attached share that document prefix. Generating notes therefore warms the cache
for the assistant, provided the model stays loaded and the slot is not replaced.
The assistant displays model loading and context-processing progress before the
first answer token. Normal AI notes and speaker notes also size their context
automatically; hierarchical summaries account for the actual block headers.

`bonsai_auto_context` defaults to true. Settings' context value is the starting
window; complete attachments plus history/instructions/output reserves can grow it
up to 262,144 total tokens. The transcript alone cannot use the whole window.
The current conservative token estimate can reject a document earlier than an
exact tokenizer would. A larger resident window is reused until unloading or
changing runtime settings; growth reloads the model and invalidates its cache.
GPU allocation failure permits one retry with the KV cache in system RAM, provided
available RAM passes a conservative headroom check. This can be slower. Insufficient
memory or an oversized document produces an explicit error, never silent truncation.
Full meeting attachments skip redundant RAG retrieval. Other models retain their
existing context policy. Installation remains download-only.

Verified on RTX 3070 with the four-minute example: native prompt processing fell
from 1,233 new tokens / 1.62 s to 61 new tokens / 0.30 s on the next question,
reusing 1,211 tokens. These are one-run measurements, not long-meeting guarantees.
The 262K window and host-cache performance have not been benchmarked on this PC.

A 43-minute recording (1,247 segments) also completed normal AI notes in 56.15 s.
The first subsequent question reused 23,689 tokens and evaluated only 196 new
tokens (0.56 s prefill, 2.94 s total). These are single-run native Bonsai 1-bit
measurements. Long transcripts render in small batches with visible progress;
the hidden speaker panel is deferred until its tab is opened.

References: [Prism server cache and timing API](https://github.com/PrismML-Eng/llama.cpp/blob/prism-b10743-adfffbe/tools/server/README.md).

## Pinned sources

- [Bonsai 27B GGUF](https://huggingface.co/prism-ml/Bonsai-27B-gguf), revision
  `f10afb355f104535e3e3e98cf7ab7795c72bd292`, `Bonsai-27B-Q1_0.gguf` (3.80 GB).
- [Ternary Bonsai GGUF](https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf), revision
  `86e89f34c93201c3dfd5e5880fedb0022fc7e34d`, `Ternary-Bonsai-27B-PQ2_0.gguf` (7.17 GB).
- [Prism llama.cpp release](https://github.com/PrismML-Eng/llama.cpp/releases/tag/prism-b10743-adfffbe).
- NVIDIA [cuBLAS 12.4.5.8](https://pypi.org/project/nvidia-cublas-cu12/12.4.5.8/) and
  [CUDA runtime 12.4.127](https://pypi.org/project/nvidia-cuda-runtime-cu12/12.4.127/).

Model repositories and runtime bundles retain their own licenses. This integration
uses the original Bonsai/Ternary models, not Bonsai 2 or an incompatible legacy Q2_0.
