(() => {
  "use strict";
  const { api, escapeHTML, formatBytes, t } = window.Meet2Notes;
  const generations = new WeakMap();
  const size = (bytes) => bytes === null || bytes === undefined ? t("storage.unavailable") : formatBytes(bytes);
  function filesystem(info) {
    if (info.total === null) return `<p>${escapeHTML(t("storage.unavailable"))}</p>`;
    return `<p>${escapeHTML(t("storage.disk", { used: size(info.used), total: size(info.total), free: size(info.free) }))}</p>` +
      `<meter min="0" max="${Math.max(1, info.total)}" value="${info.used}" aria-label="${escapeHTML(t("storage.title"))}"></meter>`;
  }
  async function refreshSettings() {
    const root = document.querySelector("#storage-usage-details");
    if (!root) return;
    const button = document.querySelector("#refresh-storage-usage");
    if (button.disabled) return;
    button.disabled = true;
    root.textContent = t("storage.loading");
    try {
      const result = await api("/api/settings/storage-usage");
      root.innerHTML = result.directories.map((item) =>
        `<section class="storage-usage-row"><strong>${escapeHTML(t(`storage.${item.name}`))}</strong>` +
        `<p>${escapeHTML(item.path)}</p><p>${escapeHTML(t("storage.folder", {
          size: `${item.complete ? "" : "≥ "}${size(item.bytes)}`, files: item.files,
        }))}</p><p>${escapeHTML(t("storage.as_of", { time: new Date(item.scanned_at).toLocaleString() }))}</p>` +
        (item.complete ? "" : `<p>${escapeHTML(t("storage.partial"))}</p>`) + filesystem(item.filesystem) + "</section>"
      ).join("") + `<section class="storage-usage-row"><strong>${escapeHTML(t("storage.system"))}</strong>` +
        `<p>${escapeHTML(result.system.path)}</p>${filesystem(result.system)}</section>`;
    } catch (error) {
      root.textContent = t("storage.error");
    } finally { button.disabled = false; }
  }
  async function refreshMeetings(root = document) {
    const nodes = [...root.querySelectorAll("[data-meeting-usage]")].filter((node) => Number(node.dataset.meetingUsage) > 0);
    if (!nodes.length) return;
    const generation = (generations.get(root) || 0) + 1;
    generations.set(root, generation);
    nodes.forEach((node) => { node.textContent = t("storage.loading"); node.title = t("storage.meeting_help"); });
    const ids = [...new Set(nodes.map((node) => node.dataset.meetingUsage))];
    try {
      const query = new URLSearchParams();
      ids.forEach((id) => query.append("meeting_ids", id));
      const result = await api(`/api/library/storage-usage?${query}`);
      if (generations.get(root) !== generation) return;
      nodes.forEach((node) => {
        const item = result.meetings.find((meeting) => String(meeting.id) === node.dataset.meetingUsage);
        node.textContent = item ? t("storage.meeting", { size: `${item.complete ? "" : "≥ "}${size(item.bytes)}` }) : t("storage.unavailable");
      });
    } catch (error) {
      if (generations.get(root) === generation) nodes.forEach((node) => { node.textContent = t("storage.error"); });
    }
  }
  document.querySelector("#refresh-storage-usage")?.addEventListener("click", refreshSettings);
  document.addEventListener("meet2notes:meetingloaded", (event) => {
    const node = document.querySelector(".meeting-heading-group [data-meeting-usage]");
    if (node) { node.dataset.meetingUsage = String(event.detail.id); void refreshMeetings(); }
  });
  document.addEventListener("localmeet:languagechange", () => { void refreshSettings(); void refreshMeetings(); });
  window.Meet2Notes.storageUsage = { refreshSettings, refreshMeetings };
  void refreshSettings();
  void refreshMeetings();
})();
