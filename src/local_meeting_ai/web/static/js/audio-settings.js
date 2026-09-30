(() => {
  "use strict";
  const { api, t } = window.Meet2Notes;
  const install = document.querySelector('#automatic-audio-install');
  const custom = document.querySelector('#automatic-audio-custom');
  const memory = document.querySelector('#automatic-model-memory');
  const status = document.querySelector('#automatic-audio-status');
  if (!install) return;
  async function refresh() {
    const info = await api('/api/engines/audio/recommendation');
    const plan = info.recommendation;
    document.querySelector('#automatic-audio-description').textContent = t('audio.plan', {
      tier: plan.tier, live: plan.live_model, final: plan.final_model,
      device: plan.device, mode: info.current.mode === 'auto' ? t('audio.auto') : t('audio.custom'),
    });
    memory.checked = info.automatic_model_memory;
  }
  install.addEventListener('click', async () => {
    install.disabled = custom.disabled = true;
    status.textContent = t('audio.installing');
    const started = Date.now();
    const timer = setInterval(() => {
      status.textContent = `${t('audio.installing')} ${Math.floor((Date.now() - started) / 1000)} s`;
    }, 1000);
    try {
      await api('/api/engines/audio/automatic', { method: 'POST' });
      clearInterval(timer);
      status.textContent = t('audio.installed');
      await refresh();
      // Reload form values from persisted settings; model loading remains lazy.
      window.location.reload();
    } catch (error) { status.textContent = error.message; }
    finally { clearInterval(timer); install.disabled = custom.disabled = false; }
  });
  custom.addEventListener('click', async () => {
    try { await api('/api/engines/audio/custom', { method: 'POST' }); await refresh(); }
    catch (error) { status.textContent = error.message; }
  });
  memory.addEventListener('change', async () => {
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify({ automatic_model_memory: memory.checked }) });
    } catch (error) { memory.checked = !memory.checked; status.textContent = error.message; }
  });
  refresh().catch(error => { status.textContent = error.message; });
  window.addEventListener('audio-settings-changed', () => {
    refresh().catch(error => { status.textContent = error.message; });
  });
})();
