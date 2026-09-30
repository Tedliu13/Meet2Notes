(() => {
  "use strict";
  const { api, renderMarkdown, t } = window.Meet2Notes;

  async function streamAnswer(url, payload, onEvent, signal) {
    const response = await fetch(`${url}/stream`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal,
    });
    // Compatibility with an app process running the previous backend until restart.
    if (response.status === 404 || response.status === 405) {
      return api(url, { method: "POST", body: JSON.stringify(payload), signal });
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === "string" ? error.detail : `HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    function event(line) {
      if (!line.trim()) return null;
      const item = JSON.parse(line);
      if (item.type === "error") throw new Error(item.message);
      onEvent(item);
      return item;
    }
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const item = event(buffer.slice(0, newline));
          buffer = buffer.slice(newline + 1);
          if (item?.type === "done") return item.result;
        }
        if (done) {
          const item = event(buffer);
          if (item?.type === "done") return item.result;
          throw new Error(t("assistant.interrupted"));
        }
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  function answerActivity(container, scrollContainer, renderAnswer = (text) => renderMarkdown(text)) {
    const body = document.createElement("div");
    body.className = "assistant-markdown";
    const status = document.createElement("div");
    status.className = "assistant-stream-status";
    status.setAttribute("role", "status");
    status.textContent = t("assistant.preparing");
    container.replaceChildren(body, status);
    container.setAttribute("aria-busy", "true");
    let text = "";
    let citations = [];
    let timer = null;
    function paint() {
      timer = null;
      const follow = scrollContainer && scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight < 100;
      body.innerHTML = renderAnswer(text, citations);
      if (follow) scrollContainer.scrollTop = scrollContainer.scrollHeight;
    }
    return {
      receive(item) {
        if (item.type === "sources") {
          citations = item.citations || [];
        } else if (item.type === "delta") {
          text += item.text;
          status.textContent = t("assistant.generating");
          if (!timer) timer = setTimeout(paint, 60);
        } else if (item.type === "context" && !text) {
          status.textContent = t("assistant.reading_context_progress", {
            processed: Number(item.processed || 0).toLocaleString(),
            total: Number(item.total || 0).toLocaleString(),
          });
        } else if (item.type === "status" && ["loading_model", "reading_context"].includes(item.phase) && !text) {
          status.textContent = t(`assistant.${item.phase}`);
        } else if (item.type === "status" && item.phase === "working" && text) {
          status.textContent = t("assistant.generating");
        }
      },
      finish(error = false) {
        clearTimeout(timer);
        paint();
        container.removeAttribute("aria-busy");
        status.classList.remove("assistant-stream-status");
        status.textContent = error ? t("assistant.interrupted") : "";
        return Boolean(text);
      },
    };
  }
  Object.assign(window.Meet2Notes, { streamAnswer, answerActivity });
})();
