# Bonsai 8B 1-bit validation — 2026-10-01

The optional [official Bonsai 8B GGUF](https://huggingface.co/prism-ml/Bonsai-8B-gguf)
is pinned to revision `48516770dd04643643e9f9019a2a349cf26c5dbd`.
Download size: 1,158,654,496 bytes. Model context ceiling: 65,536 tokens.
This differs from the 27B models' 262,144-token ceiling.

## Test conditions

Linux Docker on Windows/WSL, RTX 3070 8 GB, Ryzen 3900XT. Container restricted
to 7 GiB RAM, no swap, four CPU cores. Official pinned Prism CUDA runtime.
Two real transcript questions were streamed per run with prompt caching.
Extra CUDA allocations attempted to leave 3.3 GiB available, but another CUDA
process still reported 7,098 MiB free: WSL did **not** enforce a hard VRAM cap.
These tests are not an RTX 3050 benchmark or proof of fitting 3.3 GiB VRAM.
Runtime buffer sizes provide a more useful lower bound than global GPU usage,
which also includes the desktop, other applications and the reservation.

## Results

- Windows native runtime smoke check also passed: it correctly identified Sam
  as responsible for the PDF export in a short supplied transcript (0.11 s).
- Short product meeting: correct participant names and PDF-export explanation
  at 8K, 32K and 64K context settings. Container peak memory about 1.57 GiB.
- At 8K, GPU buffers were 1,015.99 MiB model + 324 MiB KV + 37.08 MiB compute:
  **1,377 MiB (1.35 GiB)** before CUDA/runtime overhead. This supports testing
  the 8K profile on a 4 GB card, without guaranteeing its available headroom.
- Full ITV debate: 31,051 input tokens, 64K context. First token after 25.30 s;
  subsequent question after 0.142 s with 31,047 cached tokens. Peak container
  memory 1.58 GiB. No container OOM events.
- Full-debate GPU buffers: model 1,015.99 MiB, KV cache 2,592 MiB, compute
  268.08 MiB, about **3,876 MiB before other GPU overhead**. This does not fit
  a 3.3 GiB free-VRAM budget. Do not recommend 64K fully on GPU on that laptop.
- Quality: correctly answered Nick Clegg's annual NHS figure as £8 billion,
  but incorrectly said it covered the entire UK in the follow-up. The transcript
  explicitly calls it an England-only figure. Fast caching does not guarantee
  factual correctness. Two short-meeting questions are not a broad evaluation.

## Recommendation

Offer 8B as an optional compact model with an 8K initial context. After this evaluation, automatic installation selects it for compatible NVIDIA
GPUs with at least 2,800 MiB and below 7,800 MiB VRAM, keeping the 16 GB RAM
and driver/architecture requirements. Smaller GPUs retain LFM. Use retrieved excerpts for long meetings on
small GPUs, or evaluate the automatic CPU KV fallback with sufficient free RAM;
that fallback can be slower and was not needed by these GPU runs.
The actual RTX 3050 laptop still needs testing, especially alongside speech
models and its normal desktop workload. No user's selected model was replaced.

## Saved-voice matching check

Saved eight labeled ITV speaker profiles. Matching against all sixteen saved
profiles took 15.666 s on the first run and 0.171 s with cached embeddings.
Seven of eight matched; Julie Etchingham remained unmatched because only four
of ten fragments exceeded the similarity threshold (six required), despite a
high aggregate score. Thresholds were not weakened. Profiles came from this
same recording, so this does not establish accuracy on independent recordings.
