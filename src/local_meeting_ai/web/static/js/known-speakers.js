(() => {
  "use strict";
  const { api, escapeHTML, t } = window.Meet2Notes;
  const requests = new Map();
  const ready = new Map();
  const initialSelections = new Map();
  const recognitionEnabled = new Map();
  async function open({ id = "known-speaker-picker", enabled, show = true, selected = [] } = {}) {
    const root = document.getElementById(id);
    if (!root) return;
    const version = (requests.get(id) || 0) + 1;
    requests.set(id, version);
    ready.set(id, false);
    initialSelections.set(id, [...selected]);
    const list = root.querySelector("[data-known-profile-list]");
    const status = root.querySelector("[data-known-profile-status]");
    const retry = root.querySelector("[data-known-profile-retry]");
    root.hidden = !show;
    list.innerHTML = "";
    retry.hidden = true;
    if (!show) return;
    status.textContent = t("known_speakers.loading");
    retry.onclick = () => open({ id, enabled, show, selected });
    try {
      if (enabled === undefined) {
        const preferences = await api("/api/workspace/preferences");
        enabled = preferences.diarization?.recognize_saved_speakers !== false;
      }
      if (requests.get(id) !== version) return;
      recognitionEnabled.set(id, enabled !== false);
      if (!enabled) {
        status.textContent = t("known_speakers.disabled");
        ready.set(id, true);
        return;
      }
      const profiles = await api("/api/speaker-profiles");
      if (requests.get(id) !== version) return;
      const usable = profiles.filter((profile) => profile.sample_path);
      const selectedSet = new Set(selected.map(Number));
      const missing = selected.filter((profileId) => !usable.some((profile) => Number(profile.id) === Number(profileId)));
      list.innerHTML = usable.map((profile) => `<label><input type="checkbox" data-known-profile value="${Number(profile.id)}"${selectedSet.has(Number(profile.id)) ? " checked" : ""}><span>${escapeHTML(profile.name)}</span></label>`).join("") +
        missing.map((profileId) => `<label><input type="checkbox" data-known-profile value="${Number(profileId)}" checked><span>${escapeHTML(t("known_speakers.unavailable", { id: Number(profileId) }))}</span></label>`).join("");
      initialSelections.set(id, null);
      ready.set(id, true);
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
    const initial = initialSelections.get(id);
    if (initial) return [...initial];
    return [...root.querySelectorAll("[data-known-profile]:checked")].map((input) => Number(input.value));
  }
  window.Meet2Notes.knownSpeakers = {
    open, selectedIds,
    isReady: (id) => ready.get(id) === true,
    isEnabled: (id) => recognitionEnabled.get(id) === true,
  };
})();
