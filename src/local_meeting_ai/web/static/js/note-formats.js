(() => {
  "use strict";
  const { api, escapeHTML, t, toast } = window.Meet2Notes;
  const dialog = document.querySelector("#nf-editor");
  if (!dialog) return;
  const form = document.querySelector("#nf-form");
  const sections = document.querySelector("#nf-sections");
  const error = document.querySelector("#nf-error");
  let editingId = null;
  let targetSelect = "";
  let busy = false;
  let formats = [];
  const page = document.querySelector(".nf-page");

  function addSection(value = {}) {
    if (sections.children.length >= 20) return;
    const row = document.createElement("div");
    row.className = "nf-section";
    row.innerHTML = `
      <label class="nf-field"><span>${escapeHTML(t("nf.heading"))}</span><input data-nf="title" maxlength="120" required value="${escapeHTML(value.title || "")}"></label>
      <label class="nf-field"><span>${escapeHTML(t("nf.instruction"))}</span><input data-nf="instruction" maxlength="1000" required value="${escapeHTML(value.instruction || "")}"></label>
      <label class="nf-field"><span>${escapeHTML(t("nf.output"))}</span><select data-nf="format"><option value="paragraph">${escapeHTML(t("nf.paragraph"))}</option><option value="list">${escapeHTML(t("nf.list"))}</option><option value="text">${escapeHTML(t("nf.text"))}</option></select></label>
      <label class="nf-field"><span>${escapeHTML(t("nf.item"))}</span><input data-nf="item_format" maxlength="500" value="${escapeHTML(value.item_format || "")}"></label>
      <button class="button secondary" type="button" data-nf-remove>${escapeHTML(t("nf.remove_section"))}</button>`;
    row.querySelector('[data-nf="format"]').value = value.format || "list";
    row.querySelector("[data-nf-remove]").addEventListener("click", () => {
      if (sections.children.length > 1) row.remove();
    });
    sections.append(row);
  }

  function open(source = null, duplicate = false, selectId = "") {
    if (busy) return;
    form.reset();
    editingId = source && !duplicate ? source.id : null;
    targetSelect = selectId;
    error.textContent = "";
    document.querySelector("#nf-editor-title").textContent = t(editingId ? "nf.edit" : "nf.new");
    form.elements.name.value = source ? source.name + (duplicate ? ` ${t("nf.copy_suffix")}` : "") : "";
    form.elements.description.value = source?.description || "";
    form.elements.system_prompt.value = source?.system_prompt || t("nf.default_instruction");
    form.elements.user_prompt_template.value = source?.user_prompt_template || t("nf.default_task");
    sections.replaceChildren();
    (source?.sections || [{ title: t("nf.summary"), instruction: t("nf.default_task"), format: "paragraph" }]).forEach(addSection);
    if (!dialog.open) dialog.showModal();
    form.elements.name.focus();
  }

  function button(label, action, item) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "button secondary";
    element.textContent = t(label);
    element.addEventListener("click", async () => {
      if (action === "edit" || action === "duplicate") {
        open(item, action === "duplicate");
        return;
      }
      if (action === "delete" && !window.confirm(t("nf.delete_confirm"))) return;
      element.disabled = true;
      try {
        await api(`/api/summary-templates/${item.id}${action === "default" ? "/personal-default" : ""}`, { method: action === "default" ? "POST" : "DELETE" });
        await refresh();
      } catch (failure) { toast(failure.message, "error"); }
      finally { element.disabled = false; }
    });
    return element;
  }

  function render() {
    const userId = Number(page.dataset.userId || 0);
    for (const [id, predicate] of [
      ["nf-personal", (item) => item.scope === "personal" && item.owner_id === userId],
      ["nf-shared", (item) => item.scope === "shared"],
      ["nf-others", (item) => item.scope === "personal" && item.owner_id !== userId],
    ]) {
      const list = document.getElementById(id);
      if (!list) continue;
      list.replaceChildren();
      for (const item of formats.filter(predicate)) {
        const card = document.createElement("article");
        card.className = "nf-card";
        card.innerHTML = `<h3>${escapeHTML(item.name)}</h3><p>${escapeHTML(item.description || "")}</p><small>${item.sections.length} ${escapeHTML(t("nf.sections"))}${item.is_default ? ` · ${escapeHTML(t("nf.my_default"))}` : ""}</small>`;
        if (id === "nf-others") {
          const owner = document.createElement("p");
          owner.textContent = item.owner_name || String(item.owner_id);
          card.append(owner);
        }
        const actions = document.createElement("div");
        actions.className = "nf-actions";
        if (!item.is_default) actions.append(button("nf.set_default", "default", item));
        actions.append(button("nf.duplicate", "duplicate", item));
        if (item.can_edit) actions.append(button("nf.edit", "edit", item), button("nf.delete", "delete", item));
        card.append(actions);
        list.append(card);
      }
      if (!list.children.length) {
        const empty = document.createElement("p");
        empty.className = "nf-empty";
        empty.textContent = t("nf.empty");
        list.append(empty);
      }
    }
  }

  async function refresh() {
    if (!page) return;
    const status = document.querySelector("#nf-load-status");
    status.textContent = t("nf.loading");
    try {
      formats = await api("/api/summary-templates");
      render();
      status.textContent = "";
    } catch (failure) { status.textContent = failure.message; }
  }

  document.querySelectorAll("[data-new-note-format]").forEach((element) => {
    element.addEventListener("click", () => open(null, false, element.dataset.newNoteFormat));
  });
  document.querySelectorAll("[data-nf-close]").forEach((element) => {
    element.addEventListener("click", () => { if (!busy) dialog.close(); });
  });
  dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  document.querySelector("#nf-add-section").addEventListener("click", () => addSection());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const payload = Object.fromEntries(["name", "description", "system_prompt", "user_prompt_template"].map((key) => [key, form.elements[key].value.trim()]));
    payload.scope = "personal";
    payload.sections = [...sections.children].map((row) => Object.fromEntries(["title", "instruction", "format", "item_format"].map((key) => [key, row.querySelector(`[data-nf="${key}"]`).value.trim()])));
    busy = true;
    form.querySelector('[type="submit"]').disabled = true;
    error.textContent = "";
    try {
      const saved = await api(`/api/summary-templates${editingId ? `/${editingId}` : ""}`, { method: editingId ? "PUT" : "POST", body: JSON.stringify(payload) });
      dialog.close();
      document.dispatchEvent(new CustomEvent("meet2notes:noteformatcreated", { detail: { id: saved.id, selectId: targetSelect } }));
      toast(t("nf.saved"), "success");
      await refresh();
    } catch (failure) { error.textContent = failure.message; }
    finally { busy = false; form.querySelector('[type="submit"]').disabled = false; }
  });
  // Locale loading is asynchronous; repaint once the selected language is ready.
  document.addEventListener("localmeet:languagechange", () => { if (page) render(); });
  if (page) refresh();
})();
