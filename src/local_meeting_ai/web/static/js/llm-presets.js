(() => {
  "use strict";
  // Verified against provider documentation and LiteLLM 1.103.1, 2026-10-01.
  const groups = {
    OpenAI: [
      ["openai/gpt-6-luna", "GPT-6 Luna"],
      ["openai/gpt-6.1-sol", "GPT-6.1 Sol"],
      ["openai/gpt-6-astra", "GPT-6 Astra"],
    ],
    Anthropic: [
      ["anthropic/claude-haiku-4-5", "Claude Haiku 4.5"],
      ["anthropic/claude-sonnet-5-5", "Claude Sonnet 5.5"],
      ["anthropic/claude-opus-5-5", "Claude Opus 5.5"],
    ],
    Google: [
      ["gemini/gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"],
      ["gemini/gemini-3.8-flash", "Gemini 3.8 Flash"],
      ["gemini/gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)"],
    ],
  };
  const models = new Set(Object.values(groups).flat().map(([id]) => id));
  const controls = new Map();

  function mount(inputId, baseId) {
    const input = document.getElementById(inputId);
    const select = document.getElementById(`${inputId}-preset`);
    const base = document.getElementById(baseId);
    if (!input || !select || !base) return;
    select.add(new Option("Custom", "custom"));
    Object.entries(groups).forEach(([provider, entries]) => {
      const group = document.createElement("optgroup");
      group.label = provider;
      entries.forEach(([id, label]) => group.append(new Option(label, id)));
      select.append(group);
    });
    let customModel = "";
    let customBase = "";
    const field = input.closest("label");
    function sync() {
      select.value = models.has(input.value.trim()) ? input.value.trim() : "custom";
      field.hidden = select.value !== "custom";
      if (select.value === "custom") {
        customModel = input.value;
        customBase = base.value;
      }
    }
    select.addEventListener("change", () => {
      if (select.value === "custom") {
        input.value = customModel;
        base.value = customBase;
      } else {
        if (!field.hidden) {
          customModel = input.value;
          customBase = base.value;
        }
        input.value = select.value;
        // A preset targets the provider directly, not a previously used local endpoint.
        base.value = "";
      }
      field.hidden = select.value !== "custom";
      input.dispatchEvent(new Event("change", { bubbles: true }));
      if (!field.hidden) input.focus();
    });
    controls.set(inputId, sync);
    sync();
  }
  mount("ai-litellm-model", "ai-litellm-base-url");
  mount("live-assistant-model", "live-assistant-base-url");
  window.Meet2Notes.llmPresets = {
    sync: (inputId) => controls.get(inputId)?.(),
  };
})();
