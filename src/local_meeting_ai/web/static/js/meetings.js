(() => {
  "use strict";
  const { api, escapeHTML, t, toast } = window.Meet2Notes;
  const list = document.querySelector("#meeting-library-list");
  const query = document.querySelector("#library-query");
  const scope = document.querySelector("#library-scope");
  const tag = document.querySelector("#library-tag");
  const previous = document.querySelector("#library-prev");
  const next = document.querySelector("#library-next");
  let offset = 0;
  let requestId = 0;
  let timer;
  const limit = 25;
  const params = new URLSearchParams(location.search);
  query.value = params.get("query") || "";
  scope.value = ["all", "title", "transcript"].includes(params.get("scope")) ? params.get("scope") : "all";
  let initialTag = params.get("tag") || "";

  function time(ms) {
    const seconds = Math.floor(Number(ms || 0) / 1000);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function highlight(text) {
    const terms = query.value.match(/[\p{L}\p{N}]+/gu) || [];
    if (!terms.length) return escapeHTML(text);
    const pattern = new RegExp(`(${terms.slice(0, 32).join("|")})`, "giu");
    return String(text).split(pattern).map((part, index) => index % 2 ? `<mark>${escapeHTML(part)}</mark>` : escapeHTML(part)).join("");
  }
  async function loadTags() {
    const selected = initialTag || tag.value;
    initialTag = "";
    const tags = await api("/api/tags");
    tag.innerHTML = `<option value="">${t("library.all_tags")}</option>` + tags.map((item) => `<option value="${item.id}">${escapeHTML(item.name)} (${item.meeting_count})</option>`).join("");
    tag.value = tags.some((item) => String(item.id) === selected) ? selected : "";
  }
  async function load() {
    const id = ++requestId;
    previous.disabled = true;
    next.disabled = true;
    list.setAttribute("aria-busy", "true");
    const filters = new URLSearchParams({ query: query.value.trim(), scope: scope.value, limit, offset });
    if (tag.value) filters.set("tag_id", tag.value);
    const address = new URLSearchParams();
    if (query.value.trim()) address.set("query", query.value.trim());
    if (scope.value !== "all") address.set("scope", scope.value);
    if (tag.value) address.set("tag", tag.value);
    history.replaceState(null, "", `${location.pathname}${address.size ? `?${address}` : ""}`);
    try {
      const result = await api(`/api/library?${filters}`);
      if (id !== requestId) return;
      if (offset && !result.items.length && result.total) { offset = 0; return load(); }
      document.querySelector("#visible-meeting-count").textContent = result.total;
      document.querySelector("#visible-meeting-label").textContent = t("meetings.saved_count", { count: result.total }, result.total);
      list.innerHTML = result.items.map((item) => `<article class="library-result">
        <header><div><h3><a href="/?meeting=${item.id}">${escapeHTML(item.title)}</a></h3>
        <small>${escapeHTML(new Date(item.meeting_date).toLocaleString(Meet2Notes.currentLanguage, { dateStyle: "medium", timeStyle: "short" }))} · ${time(item.duration_ms)}${item.audio_deleted_at ? ` · ${t("library.audio_deleted")}` : ""}</small></div>
        <div class="library-row-actions"><button type="button" class="button secondary" data-edit-tags="${item.id}" data-title="${escapeHTML(item.title)}">${t("library.edit_tags")}</button><button type="button" class="text-button danger" data-delete-meeting="${item.id}" data-title="${escapeHTML(item.title)}">${t("library.delete")}</button></div></header>
        ${item.description ? `<p>${escapeHTML(item.description)}</p>` : ""}
        <div class="meeting-tags">${item.tags.map((label) => `<button type="button" class="meeting-tag" data-filter-tag="${label.id}">${escapeHTML(label.name)}</button>`).join("")}</div>
        ${item.matches.length ? `<div class="library-hits">${item.matches.map((hit) => `<a class="library-hit" href="/?meeting=${item.id}&segment=${hit.segment_id}"><strong>${time(hit.start_ms)} · ${escapeHTML(hit.speaker)}</strong>${highlight(hit.text)}</a>`).join("")}</div>` : ""}
      </article>`).join("") || `<div class="library-message"><strong>${t("library.empty")}</strong><p>${t("library.empty_help")}</p></div>`;
      document.querySelector("#library-page").textContent = result.total ? `${offset + 1}–${Math.min(offset + limit, result.total)} / ${result.total}` : "0 / 0";
      previous.disabled = offset === 0;
      next.disabled = offset + limit >= result.total;
    } catch (error) {
      if (id !== requestId) return;
      list.innerHTML = `<div class="library-message" role="alert">${escapeHTML(error.message)} <button type="button" class="button secondary" id="library-retry">${t("library.retry")}</button></div>`;
    } finally { if (id === requestId) list.removeAttribute("aria-busy"); }
  }
  function search() { offset = 0; clearTimeout(timer); load(); }
  query.addEventListener("input", () => {
    ++requestId;
    clearTimeout(timer);
    timer = setTimeout(search, 220);
  });
  scope.addEventListener("change", search);
  tag.addEventListener("change", search);
  previous.addEventListener("click", () => { offset = Math.max(0, offset - limit); load(); });
  next.addEventListener("click", () => { offset += limit; load(); });
  list.addEventListener("click", async (event) => {
    const edit = event.target.closest("[data-edit-tags]");
    if (edit) Meet2Notes.openTags(edit.dataset.editTags, edit.dataset.title);
    const filter = event.target.closest("[data-filter-tag]");
    if (filter) { tag.value = filter.dataset.filterTag; search(); }
    if (event.target.closest("#library-retry")) load();
    const remove = event.target.closest("[data-delete-meeting]");
    if (!remove || !window.confirm(t("library.delete_meeting_confirm", { title: remove.dataset.title }))) return;
    remove.disabled = true;
    try {
      await api(`/api/meetings/${remove.dataset.deleteMeeting}`, { method: "DELETE" });
      await loadTags();
      await load();
    } catch (error) { toast(error.message, "error"); remove.disabled = false; }
  });
  document.addEventListener("meet2notes:tagschanged", () => loadTags().then(load).catch((error) => toast(error.message, "error")));
  document.addEventListener("localmeet:languagechange", () => loadTags().then(load).catch((error) => toast(error.message, "error")));
  loadTags().then(load).catch((error) => {
    list.innerHTML = `<p class="library-message">${escapeHTML(error.message)} <button id="library-retry" type="button">${t("library.retry")}</button></p>`;
  });
})();
