(() => {
  "use strict";
  const { api, t } = window.Meet2Notes;
  const url = document.querySelector("#ai-ollama-url");
  const select = document.querySelector("#ai-ollama-model");
  const status = document.querySelector("#ai-ollama-status");
  const info = document.querySelector("#ai-ollama-model-info");
  const refreshButton = document.querySelector("#ai-ollama-refresh");
  let controller;
  let models = [];
  let verifiedUrl = null;
  let preferredModel = "";
  let endpointIsLocal = true;

  function showModel() {
    const model = models.find((item) => item.name === select.value);
    if (!model) { info.textContent = ""; return; }
    const size = Number(model.size || 0) / 1e9;
    const location = model.cloud ? t("ollama.cloud")
      : endpointIsLocal ? t("ollama.local") : t("ollama.server");
    info.textContent = `${location} · ${model.parameter_size || ""} · ${size.toFixed(2)} GB`;
    if (model.context_length) info.textContent += ` · ${t("ollama.context", { count: model.context_length })}`;
  }

  function invalidate() {
    controller?.abort();
    verifiedUrl = null;
    select.disabled = true;
    models = [];
    info.textContent = "";
  }

  async function refresh() {
    const requested = url.value.trim();
    const previous = select.value || preferredModel;
    invalidate();
    const request = new AbortController();
    controller = request;
    refreshButton.disabled = true;
    status.textContent = t("ollama.checking");
    select.replaceChildren(new Option(t("ollama.choose"), ""));
    try {
      const result = await api(`/api/runtimes/ollama${requested ? `?base_url=${encodeURIComponent(requested)}` : ""}`, { signal: request.signal });
      if (controller !== request || request.signal.aborted) return;
      url.value = result.base_url;
      endpointIsLocal = result.local_endpoint;
      models = result.models || [];
      verifiedUrl = result.base_url;
      const states = {
        ready: t("ollama.state.ready"), empty: t("ollama.state.empty"),
        stopped: t("ollama.state.stopped"), unreachable: t("ollama.state.unreachable"),
        connection_error: t("ollama.state.connection_error"),
      };
      status.textContent = states[result.state] || states.connection_error;
      if (result.version) status.textContent += ` · Ollama ${result.version}`;
      if (result.unverified_models) status.textContent += ` ${t("ollama.unverified")}`;
      for (const model of models) {
        select.add(new Option(`${model.name}${model.cloud ? ` · ${t("ollama.cloud")}` : ""}`, model.name));
      }
      if (models.some((model) => model.name === previous)) select.value = previous;
      else if (previous) status.textContent += ` ${t("ollama.previous_missing")}`;
      select.disabled = models.length === 0;
      showModel();
    } catch (error) {
      if (controller !== request || request.signal.aborted) return;
      status.textContent = `${t("ollama.state.connection_error")} ${error.message}`;
    } finally {
      if (controller === request) refreshButton.disabled = false;
    }
  }

  select.addEventListener("change", () => {
    preferredModel = select.value;
    const model = models.find((item) => item.name === select.value);
    const context = document.querySelector("#ai-context-length");
    if (model?.context_length >= 2048) {
      context.value = Math.min(Number(context.value), model.context_length, 131072);
    }
    showModel();
  });
  url.addEventListener("input", () => {
    invalidate();
    refreshButton.disabled = false;
    status.textContent = t("ollama.refresh_needed");
  });
  refreshButton.addEventListener("click", refresh);

  window.Meet2NotesOllama = {
    configure(config) {
      preferredModel = /^(ollama|ollama_chat)\//.test(config.model || "")
        ? config.model.replace(/^(ollama|ollama_chat)\//, "") : "";
      url.value = config.base_url || "";
      select.replaceChildren(new Option(t("ollama.choose"), ""));
      refresh();
    },
    selection() {
      const model = models.find((item) => item.name === select.value);
      if (!model || select.disabled || url.value.trim() !== verifiedUrl) {
        throw new Error(t("ollama.select_required"));
      }
      if (model.context_length && Number(document.querySelector("#ai-context-length").value) > model.context_length) {
        throw new Error(t("ollama.context_exceeded", { count: model.context_length }));
      }
      return { model: `ollama_chat/${model.name}`, base_url: verifiedUrl };
    },
  };
})();
