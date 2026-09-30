# Select an Ollama LLM

Open **Settings → AI engine**, select **Ollama**, choose a model, and click
**Save AI settings**. Meet2Notes discovers the service and reads the available
models. Selecting the Ollama catalog entry opens the configuration; it does not
replace the saved engine until a model is chosen and the form is saved.

The default address is `http://127.0.0.1:11434`, or `OLLAMA_HOST` when provided
to the Meet2Notes process. You can enter another HTTP(S) address for a different
port, another computer, or Docker. Click **Refresh models** after changing it.
In Docker, localhost refers to the container, not the host computer.

Discovery uses Ollama's `/api/version`, `/api/tags`, and `/api/show` endpoints.
It runs from the Meet2Notes backend on Windows, Linux and macOS, so no browser
CORS change is needed. It looks for the executable in PATH and common installation
locations to distinguish an installed but unreachable local service. Custom
installation locations may not be detected while the service is stopped.

Only models advertising text generation (`completion`) are offered. Embedding-only
models are excluded. Unverified models are omitted with a retry message. No model
is automatically selected; a saved selection is retained when it is still present.
If it disappears, the saved settings remain intact until you choose a replacement.
The discovery has timeouts and inspects at most 100 models per refresh.

Model details show disk size and the advertised context limit. Disk size is not a
RAM/VRAM estimate. Models advertising cloud execution are labeled; models on a
remote server are also distinguished from models on the local service. Choosing
a cloud model sends meeting text to its cloud provider when you generate notes.

The configured context is passed to Ollama as `num_ctx`, rather than being used
only to split transcripts in Meet2Notes. The selector caps context to the model's
advertised limit when changing models and validates it before saving. Larger
contexts use more memory; the supported maximum is not a hardware recommendation.
The dedicated profile disables thinking output for direct meeting notes and uses
Ollama chat through LiteLLM. It does not forward another provider's shared API key.
Authenticated/proxied setups can still use the advanced **Custom local / remote
via LiteLLM** option.

Meet2Notes does not start Ollama, install it, pull models, or change its settings.
If the service is stopped, open Ollama or start its service and refresh. For an
empty list, add a text LLM using Ollama first. Discovery and selecting a model do
not load it into memory; generating notes does. The context change also applies
to manually configured `ollama/` and `ollama_chat/` models.

The dedicated selector is in AI Engine (AI Notes). Live Assistant and RAG keep
their existing configuration, including the manual LiteLLM connection option.

See [Ollama's model list API](https://docs.ollama.com/api/tags) and
[model capabilities](https://docs.ollama.com/api-reference/show-model-details).

## Assistant streaming

Settings > AI Engine > **Stream assistant responses** controls progressive answers
in Meeting Assistant. Live Assistant has its own switch in its settings. Both are
enabled by default and also work with local GGUF and LiteLLM providers. Turning
streaming off displays the complete answer at the end; an activity indicator is
always shown while waiting, including model loading and context preparation.

Providers that explicitly reject streaming fall back to a buffered answer. A
connection failure after partial output is shown as an interrupted answer and is
not automatically retried. Source references and conversation history are updated
only after successful completion. Restart Meet2Notes after updating the backend.
