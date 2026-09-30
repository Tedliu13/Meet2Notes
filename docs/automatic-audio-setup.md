# Automatic audio profiles

Fresh installations select a coordinated live/final transcription bundle and
Sherpa-ONNX diarization. Windows, Linux and Pinokio call the same Python policy.
Existing audio preferences are preserved, including a manually selected diarizer.
Settings → Transcription offers an explicit **Install and use recommended profile**
action and a custom mode. Changing individual audio settings switches to custom mode.

| Hardware | Live | Final | Speakers |
|---|---|---|---|
| Very limited CPU/RAM | Whisper tiny INT8 | base INT8 | Sherpa CPU |
| Modest CPU/RAM | base INT8 | base INT8 | Sherpa CPU |
| Modern CPU, at least approximately 16 GB RAM | base INT8 | small INT8 | Sherpa CPU |
| Validated NVIDIA CUDA, 4–6 GB VRAM | small | turbo | Sherpa CPU |
| Validated NVIDIA CUDA, 8 GB VRAM | small | turbo | Sherpa CPU |
| Validated NVIDIA CUDA, 16 GB+ VRAM | small | turbo | Sherpa CPU |

GPU selection checks CTranslate2 availability as well as NVIDIA hardware/driver
information. GPUs are not summed. The CPU policy also considers logical processor
count and RAM. Apple Silicon and AMD/Intel GPUs currently use the portable CPU
path. Whisper.cpp acceleration remains a future integration.

More VRAM does not automatically select a slower model. Large-v3 remains an
explicit quality option. Pyannote Community-1 remains optional because its model
download requires Hugging Face access acceptance and a token. Parakeet, Nemotron,
VibeVoice and diarize remain available through their existing settings.

The installer downloads files without loading audio models or running inference.
Preferences are committed only after both Whisper models and Sherpa files exist;
download failures or concurrent preference changes leave previous choices intact.
`--whisper-model auto` is the new CLI default. Explicit model names remain accepted.
Pinokio's Torch bootstrap now chooses CPU/CUDA instead of forcing a CPU build;
macOS uses the normal platform wheel. Updating does not silently replace selections.

## Memory and fallback

The bundle enables **Release idle models between processing stages**. This option
can be disabled independently in Settings. It conservatively serializes managed
engine operations and releases other idle resident engines when changing stages.
It never evicts an inference in flight, including when its HTTP caller disconnects.
Explicit unload requests made during inference are deferred until processing ends.
Repeated summary/assistant requests keep the same summary runtime and KV prefix.
Switching to another engine, including a new audio task, can invalidate that cache.

This is a conservative residency policy, not a VRAM reservation system: external
applications can still consume memory. Automatic memory mode first checks free
VRAM before cold Whisper loads, using conservative
headroom thresholds. When GPU memory is already scarce it selects CPU INT8 for
that request without changing the saved preference. A runtime CUDA/memory failure
retries Whisper once on CPU INT8 with the same model, only before any segments
have been emitted. It logs the fallback. A failure after partial output is reported
instead of silently duplicating or replacing transcript segments.

## Long diarize jobs

The isolated `diarize` worker does not expose a reliable intermediate percentage.
Meet2Notes reports elapsed time every 15 seconds and displays indeterminate progress
during that stage. Completion percentages resume for matching and saving results.
Cancellation is checked while waiting for the worker, rather than only after its
final response. The blocking pipe read no longer defeats cancellation or the
24-hour watchdog. Only the private diarize subprocess is terminated on cancellation.

Accuracy and speed depend on the recording. The policy is intentionally not a
claim that Turbo or Sherpa is the most accurate model for every language/meeting.
