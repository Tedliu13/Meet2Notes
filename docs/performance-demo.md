# Bonsai performance demo

[Watch the narrated demo on YouTube](https://www.youtube.com/watch?v=pDUrVM5XTZw)
(2 min 6 sec). Recorded on 2026-09-30 using the changes included in 0.8.0.

## Setup

- NVIDIA GeForce RTX 3070: 8 GB VRAM; 32 GB system RAM.
- Native Bonsai 27B Q1_0 (1-bit), through the managed Prism llama.cpp runtime.
- A 65,536-token context window, compressed KV cache and one resident model slot.
- Source: *The ITV Leaders' Debate* (2015), a 2 h 18 min 17.7 sec recording.
- The existing completed transcript was attached in the real Prompt interface.
  Its first request, including prompt instructions, processed 42,343 input tokens.
- No cloud inference. This measures question answering over a completed transcript,
  not transcription speed or transcription accuracy.

## Results from this run

| Request | First text | Complete answer | Cached input tokens | New input tokens | Output tokens | Native generation rate |
|---|---:|---:|---:|---:|---:|---:|
| Initial question about the debate | 74.63 s | 75.68 s | 0 | 42,343 | 33 | 30.7 tokens/s |
| Ed Miliband's main proposals | 1.64 s | 3.95 s | 42,144 | 266 | 73 | 31.2 tokens/s |
| Cameron vs. Sturgeon on austerity | 1.62 s | 4.37 s | 42,196 | 323 | 87 | 31.3 tokens/s |
| Nick Clegg's NHS funding proposal | 1.83 s | 2.61 s | 42,303 | 335 | 25 | 31.0 tokens/s |

The questions requested short answers: three bullets under 90 words, two bullets
under 80 words, and one sentence respectively. Longer answers take more time.
The first request spent approximately 70.48 seconds processing its input.

## How the measurements were captured

Passive instrumentation observed the application's original streamed response
events and native runtime timing data without changing the prompts or responses.
First-text latency runs from the server receiving the request to the first
nonempty answer delta; total time runs to response completion. These are server
measurements, not browser paint or network latency measurements. Generation rate
is the native runtime's reported value and excludes prompt processing.

The three follow-up questions were consecutive after the initial cold request,
using the same loaded model and complete transcript attachment. Cached/new token
counts come from the runtime, not estimates from the transcript's character count.
This demonstrates reuse across questions; the separate AI-notes-to-assistant cache
handoff is implemented but is not the warming sequence measured in this video.

Screen captures were sampled at 8 fps and placed on a 30 fps timeline with their
original wall-clock spacing. Follow-up answers are not accelerated or rewritten.
The initial wait is abbreviated and labeled, and completed answers include
explicitly labeled reading holds. Voiceover and background music were added later.

## Interpretation

These are observations from one machine and one run, not a hardware-independent
benchmark or an accuracy evaluation. The recording duration describes the source
media; the measured token count describes the context actually supplied.

This validates a 65,536-token window on this setup, not 262,144 tokens on an 8 GB
GPU. The model's maximum total window includes instructions, history and output.
Available RAM/VRAM, answer length and other active models affect practical limits.

The cache belongs to the running model process. Unloading it, changing its context
allocation, changing the document prefix, or another meeting replacing its slot
can require processing the context again. New questions and answers still require
computation. See [automatic LLM setup](automatic-llm-setup.md).

The preview image is a frame from the demo's title card. Debate footage is credited
to ITV News in the video and shown as the source material being queried.

## Answer review and NHS clarification

All 1,306 segments of the supplied transcript were reviewed against the three
recorded answers. Miliband's proposals and the Cameron/Sturgeon comparison are
supported by their statements. Clegg's £8 billion amount and deadline also match
his words, but the short answer omits the scope: **NHS England**, not the entire UK.
Sturgeon explicitly makes that distinction later in the debate.

In the original source recording, Clegg gives the amount and deadline at about
49:33–49:48, and Sturgeon clarifies England at about 57:55–58:10. These are source
recording times, not timestamps in the two-minute demo. The wider funding context
was an increase in annual funding to be reached by 2020/21; see
[NHS England's 2014–15 Annual Report, page 11](https://www.england.nhs.uk/wp-content/uploads/2015/07/nhse-annual-report-2014-15.pdf#page=12).
The captured model answer is preserved unchanged. This transcript review does not
certify every ASR word against the original audio or independently validate the
politicians' economic claims.
