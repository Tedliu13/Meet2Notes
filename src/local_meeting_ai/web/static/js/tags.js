(() => {
  "use strict";
  const { api, escapeHTML, t, toast } = window.Meet2Notes;
  const dialog = document.querySelector("#tags-dialog");
  if (!dialog) return;
  const options = document.querySelector("#tags-options");
  const error = document.querySelector("#tags-error");
  let currentId = null;
  let selected = new Set();
  let tags = [];
  let busy = false;

  function render() {
    options.innerHTML = tags.map((tag) => `<div class="tag-option">
      <label><input type="checkbox" value="${tag.id}" ${selected.has(tag.id) ? "checked" : ""}>
      <span>${escapeHTML(tag.name)}</span><small>${tag.meeting_count}</small></label>
      <button type="button" class="text-button" data-rename-tag="${tag.id}">${t("library.rename")}</button>
      <button type="button" class="text-button danger" data-delete-tag="${tag.id}">${t("library.delete")}</button>
    </div>`).join("") || `<p>${t("library.no_tags")}</p>`;
  }

  async function operate(action) {
    if (busy) return;
    busy = true;
    error.textContent = "";
    dialog.querySelectorAll("button, input").forEach((item) => { item.disabled = true; });
    try { await action(); } catch (failure) { error.textContent = failure.message; }
    finally {
      busy = false;
      dialog.querySelectorAll("button, input").forEach((item) => { item.disabled = false; });
    }
  }

  async function refresh() {
    tags = await api("/api/tags");
    render();
    document.dispatchEvent(new CustomEvent("meet2notes:tagschanged"));
  }

  window.Meet2Notes.openTags = async (meetingId, title) => {
    if (busy || dialog.open) return;
    currentId = Number(meetingId);
    error.textContent = "";
    document.querySelector("#tags-meeting-name").textContent = title;
    document.querySelector("#new-tag-name").value = "";
    try {
      const [all, assigned] = await Promise.all([
        api("/api/tags"), api(`/api/meetings/${currentId}/tags`),
      ]);
      tags = all;
      selected = new Set(assigned.map((tag) => tag.id));
      render();
      dialog.showModal();
    } catch (failure) { toast(failure.message, "error"); }
  };

  options.addEventListener("change", (event) => {
    if (event.target.matches('input[type="checkbox"]')) {
      const id = Number(event.target.value);
      if (event.target.checked) selected.add(id); else selected.delete(id);
    }
  });
  options.addEventListener("click", (event) => {
    const rename = event.target.closest("[data-rename-tag]");
    const remove = event.target.closest("[data-delete-tag]");
    if (rename) {
      const tag = tags.find((item) => item.id === Number(rename.dataset.renameTag));
      const name = window.prompt(t("library.rename_tag"), tag.name);
      if (name === null || name === tag.name) return;
      operate(async () => {
        await api(`/api/tags/${tag.id}`, { method: "PATCH", body: JSON.stringify({ name }) });
        await refresh();
      });
    }
    if (remove) {
      const id = Number(remove.dataset.deleteTag);
      const tag = tags.find((item) => item.id === id);
      if (!window.confirm(t("library.delete_tag_confirm", { name: tag.name }))) return;
      operate(async () => {
        await api(`/api/tags/${id}`, { method: "DELETE" });
        selected.delete(id);
        await refresh();
      });
    }
  });
  document.querySelector("#create-tag").addEventListener("click", () => operate(async () => {
    const input = document.querySelector("#new-tag-name");
    const tag = await api("/api/tags", { method: "POST", body: JSON.stringify({ name: input.value }) });
    selected.add(tag.id);
    input.value = "";
    await refresh();
  }));
  document.querySelector("#new-tag-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); document.querySelector("#create-tag").click(); }
  });
  document.querySelector("#tags-form").addEventListener("submit", (event) => {
    event.preventDefault();
    operate(async () => {
      await api(`/api/meetings/${currentId}/tags`, { method: "PUT", body: JSON.stringify({ tag_ids: [...selected] }) });
      dialog.close();
      document.dispatchEvent(new CustomEvent("meet2notes:tagschanged"));
      toast(t("library.tags_saved"));
    });
  });
  dialog.querySelectorAll("[data-tags-close]").forEach((button) => button.addEventListener("click", () => { if (!busy) dialog.close(); }));
  dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
})();
