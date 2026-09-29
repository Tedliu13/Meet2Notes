(() => {
  "use strict";
  const select = document.querySelector("#assistant-action");
  if (!select) return;
  const { api, t, toast, escapeHTML } = window.Meet2Notes;
  const dialog = document.querySelector("#quick-action-dialog");
  const saved = document.querySelector("#quick-action-saved");
  const name = document.querySelector("#quick-action-name");
  const prompt = document.querySelector("#quick-action-prompt");
  const error = document.querySelector("#quick-action-error");
  const use = document.querySelector("#assistant-action-use");
  const builtins = ["followup", "decisions", "tasks", "brief"];
  let actions = [];
  let busy = false;
  function options() {
    const selection = select.value;
    select.innerHTML = `<option value="">${t("actions.title")}</option>` + builtins.map((id) => `<option value="${id}">${escapeHTML(t(`actions.${id}.name`))}</option>`).join("") + actions.map((item) => `<option value="custom-${item.id}">${escapeHTML(item.name)}</option>`).join("");
    if ([...select.options].some((item) => item.value === selection)) select.value = selection;
    use.disabled = !select.value;
    saved.innerHTML = `<option value="">${t("actions.new")}</option>` + actions.map((item) => `<option value="${item.id}">${escapeHTML(item.name)}</option>`).join("");
  }
  async function load() { actions = await api("/api/assistant/actions"); options(); }
  function edit() {
    const action = actions.find((item) => String(item.id) === saved.value);
    name.value = action?.name || "";
    prompt.value = action?.prompt || "";
    document.querySelector("#quick-action-delete").disabled = !action;
    error.textContent = "";
  }
  select.addEventListener("change", () => { use.disabled = !select.value; });
  use.addEventListener("click", () => {
    const composer = document.querySelector("#post-meeting-assistant-form");
    const question = document.querySelector("#post-meeting-assistant-question");
    if (composer.dataset.busy) return;
    const action = actions.find((item) => `custom-${item.id}` === select.value);
    const value = action?.prompt || (builtins.includes(select.value) ? t(`actions.${select.value}.prompt`) : "");
    if (!value) return;
    if (question.value.trim() && question.value !== value && !window.confirm(t("actions.replace"))) return;
    question.value = value;
    question.dispatchEvent(new Event("input", { bubbles: true }));
    question.focus();
  });
  document.querySelector("#assistant-action-manage").addEventListener("click", async () => {
    try {
      await load();
      saved.value = select.value.startsWith("custom-") ? select.value.slice(7) : "";
      edit();
      if (builtins.includes(select.value)) {
        name.value = t(`actions.${select.value}.name`);
        prompt.value = t(`actions.${select.value}.prompt`);
      }
      if (!dialog.open) dialog.showModal();
    } catch (failure) { toast(failure.message, "error"); }
  });
  saved.addEventListener("change", edit);
  async function operate(action) {
    if (busy) return;
    busy = true;
    error.textContent = "";
    dialog.querySelectorAll("button, input, textarea, select").forEach((item) => { item.disabled = true; });
    try { await action(); } catch (failure) { error.textContent = failure.message; }
    finally {
      busy = false;
      dialog.querySelectorAll("button, input, textarea, select").forEach((item) => { item.disabled = false; });
      document.querySelector("#quick-action-delete").disabled = !saved.value;
    }
  }
  document.querySelector("#quick-action-form").addEventListener("submit", (event) => {
    event.preventDefault();
    operate(async () => {
      const id = saved.value;
      const action = await api(`/api/assistant/actions${id ? `/${id}` : ""}`, { method: id ? "PATCH" : "POST", body: JSON.stringify({ name: name.value, prompt: prompt.value }) });
      await load();
      select.value = `custom-${action.id}`;
      use.disabled = false;
      saved.value = String(action.id);
      edit();
      toast(t("actions.saved_ok"));
    });
  });
  document.querySelector("#quick-action-delete").addEventListener("click", () => {
    if (!saved.value || !window.confirm(t("actions.delete_confirm"))) return;
    operate(async () => { await api(`/api/assistant/actions/${saved.value}`, { method: "DELETE" }); await load(); edit(); });
  });
  document.querySelector("#quick-action-close").addEventListener("click", () => { if (!busy) dialog.close(); });
  dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  document.addEventListener("localmeet:languagechange", options);
  load().catch((failure) => { options(); toast(failure.message, "error"); });
})();
