(() => {
  "use strict";
  const { api, escapeHTML, t } = window.Meet2Notes;
  const requests = new Map();
  async function open({ id = "known-speaker-picker", enabled, show = true } = {}) {
    const root = document.getElementById(id);
    if (!root) return;
    const version = (requests.get(id) || 0) + 1;
    requests.set(id, version);
    const list = root.querySelector("[data-known-profile-list]");
    const status = root.querySelector("[data-known-profile-status]");
    const retry = root.querySelector("[data-known-profile-retry]");
    root.hidden = !show;
    list.innerHTML = "";
    retry.hidden = true;
    if (!show) return;
    status.textContent = t("known_speakers.loading");
    retry.onclick = () => open({ id, enabled, show });
    try {
      if (enabled === undefined) {
        const preferences = await api("/api/workspace/preferences");
        enabled = preferences.diarization?.recognize_saved_speakers !== false;
      }
      if (requests.get(id) !== version) return;
      if (!enabled) {
        status.textContent = t("known_speakers.disabled");
        return;
      }
      const profiles = await api("/api/speaker-profiles");
      if (requests.get(id) !== version) return;
      const usable = profiles.filter((profile) => profile.sample_path);
      list.innerHTML = usable.map((profile) => `<label><input type="checkbox" data-known-profile value="${Number(profile.id)}"><span>${escapeHTML(profile.name)}</span></label>`).join("");
      status.textContent = usable.length ? t("known_speakers.optional") : t("known_speakers.empty");
    } catch (error) {
      if (requests.get(id) !== version) return;
      status.textContent = t("known_speakers.error");
      retry.hidden = false;
    }
  }
  function selectedIds(id = "known-speaker-picker") {
    const root = document.getElementById(id);
    if (!root || root.hidden) return [];
    return [...root.querySelectorAll("[data-known-profile]:checked")].map((input) => Number(input.value));
  }
  window.Meet2Notes.knownSpeakers = { open, selectedIds };
})();
