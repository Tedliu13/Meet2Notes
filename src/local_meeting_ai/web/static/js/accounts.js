(() => {
  "use strict";
  if (document.body.dataset.hosted !== "true") return;
  const { api, escapeHTML, toast } = window.Meet2Notes;
  document.querySelector("#account-logout")?.addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); location.assign("/login"); }
    catch (error) { toast(error.message, "error"); }
  });
  const users = document.querySelector("#account-users");
  async function loadUsers() {
    const list = await api("/api/users");
    users.innerHTML = list.map((user) => `<form class="user-row" data-user="${user.id}">
      <label class="field">${escapeHTML(user.username)}${user.is_admin ? " · 管理者" : ""}<input name="name" value="${escapeHTML(user.name)}" maxlength="100" required aria-label="姓名"></label>
      <label class="field">重設密碼<input name="password" type="password" minlength="12" maxlength="256" autocomplete="new-password" placeholder="留空保留原密碼"></label>
      <span>${user.active ? "啟用中" : "已停用"}</span><button class="button secondary" type="submit">儲存</button>
      ${user.is_admin ? "" : `<button class="button secondary" type="button" data-activation="${user.active ? "false" : "true"}">${user.active ? "停用" : "啟用"}</button><button class="button danger" type="button" data-delete-user>刪除</button>`}
      </form>`).join("");
  }
  async function changeUser(form, method, values) {
    const buttons = form.querySelectorAll("button");
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await api(`/api/users/${form.dataset.user}`, { method, ...(values ? { body: JSON.stringify(values) } : {}) });
      toast("帳號已更新");
      await loadUsers();
    } catch (error) { toast(error.message, "error"); }
    finally { buttons.forEach((button) => { button.disabled = false; }); }
  }
  if (users) {
    users.addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.target;
      const values = { name: form.elements.name.value.trim() };
      if (form.elements.password.value) values.password = form.elements.password.value;
      changeUser(form, "PATCH", values);
    });
    users.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const form = button.closest("form");
      if (button.hasAttribute("data-activation")) changeUser(form, "PATCH", { active: button.dataset.activation === "true" });
      if (button.hasAttribute("data-delete-user") && confirm("刪除此帳號？使用者將無法登入；既有會議及建立者姓名會保留。")) changeUser(form, "DELETE");
    });
    loadUsers().catch((error) => { users.textContent = error.message; });
  }
  const people = document.querySelector("#meeting-people");
  const dialog = document.querySelector("#meeting-managers-dialog");
  const options = document.querySelector("#meeting-manager-options");
  let access = null;
  let targetId = null;
  let generation = 0;
  async function loadAccess(id) {
    const request = ++generation;
    document.body.dataset.canManage = "false";
    const pendingTitle = document.querySelector("#transcription-title-display");
    if (pendingTitle) pendingTitle.disabled = true;
    if (people) people.textContent = "正在讀取會議權限…";
    if (dialog?.open) dialog.close();
    targetId = id;
    const result = await api(`/api/meetings/${id}/access`);
    if (request !== generation) return;
    access = result;
    document.body.dataset.canManage = String(access.can_manage);
    const title = document.querySelector("#transcription-title-display");
    if (title) title.disabled = !access.can_manage;
    if (access.can_manage) document.querySelectorAll("[data-permission-read-only]").forEach((input) => {
      input.readOnly = false;
      delete input.dataset.permissionReadOnly;
    });
    if (people) {
      people.innerHTML = (access.creator ? `<span class="person-label">建立者：${escapeHTML(access.creator.name)}${access.creator.deleted ? "（帳號已刪除）" : ""}</span>` : "")
        + access.managers.map((user) => `<span class="person-label">共同管理：${escapeHTML(user.name)}</span>`).join("")
        + (access.can_share ? '<button type="button" class="text-button" id="manage-meeting-people">共同管理</button>' : "")
        + (!access.can_manage ? '<span class="person-label">唯讀：可觀看、播放及使用會議工具</span>' : "");
    }
    applyReadOnly();
  }
  function applyReadOnly() {
    if (document.body.dataset.canManage !== "false") return;
    document.querySelectorAll(".segment-editor, [data-speaker-name], #ai-report-editor").forEach((input) => {
      if (!input.readOnly) input.dataset.permissionReadOnly = "true";
      input.readOnly = true;
    });
  }
  if (people) {
    document.querySelector("#close-meeting-managers").addEventListener("click", () => dialog.close());
    const page = document.querySelector("[data-meeting-id]");
    if (page?.dataset.meetingId) loadAccess(page.dataset.meetingId).catch((error) => toast(error.message, "error"));
    document.addEventListener("meet2notes:meetingloaded", (event) => loadAccess(event.detail.id).catch((error) => toast(error.message, "error")));
    new MutationObserver(applyReadOnly).observe(document.querySelector("#transcription-workspace"), { childList: true, subtree: true });
    people.addEventListener("click", async (event) => {
      if (!event.target.closest("#manage-meeting-people")) return;
      try {
        const directory = await api("/api/users/directory");
        options.innerHTML = directory.filter((user) => user.id !== access.creator?.id).map((user) => `<label><input type="checkbox" value="${user.id}" ${access.managers.some((manager) => manager.id === user.id) ? "checked" : ""}>${escapeHTML(user.name)}</label>`).join("") || "目前沒有其他可指定的使用者";
        if (!dialog.open) dialog.showModal();
      } catch (error) { toast(error.message, "error"); }
    });
    document.querySelector("#save-meeting-managers").addEventListener("click", async (event) => {
      const button = event.target;
      const id = targetId;
      button.disabled = true;
      try {
        const user_ids = [...options.querySelectorAll("input:checked")].map((input) => Number(input.value));
        await api(`/api/meetings/${id}/managers`, { method: "PUT", body: JSON.stringify({ user_ids }) });
        dialog.close();
        await loadAccess(id);
        toast("共同管理者已更新");
      } catch (error) { toast(error.message, "error"); }
      finally { button.disabled = false; }
    });
  }
})();
