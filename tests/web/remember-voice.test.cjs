const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
const catalog = JSON.parse(fs.readFileSync('src/local_meeting_ai/web/static/locales/zh-TW.json', 'utf8'));

function workspace({ failSave = false, failRefresh = false } = {}) {
  const button = { disabled: false, textContent: '記住此聲紋', addEventListener(type, callback) { this.click = callback; } };
  const dialog = { open: true, close() { this.open = false; }, showModal() { this.open = true; } };
  const nodes = { '#remember-renamed-voice': button, '#remember-voice-title': {}, '#remember-voice-description': {} };
  const requests = [], messages = [];
  const state = {
    activeTranscriptionId: 7, pendingRememberSpeakerId: 1,
    lastDetail: { speakers: [{ id: 1, display_name: '甲' }, { id: 2, display_name: '乙' }] },
    rememberVoiceDialog: dialog,
    document: { querySelector: selector => nodes[selector] },
    t: (key, values = {}) => catalog[key].replace(/\{(\w+)\}/g, (_, name) => values[name]),
    toast: (message, type) => messages.push({ message, type }),
    async api(path) { requests.push(path); if (failSave) throw Error('儲存失敗'); return { name: '講者' }; },
    async selectTranscription() { if (failRefresh) throw Error('refresh failed'); },
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('  async function rememberSpeakerVoice('),
    source.indexOf('  function setAudioPlaybackButton(')), state);
  const start = source.indexOf('  document.querySelector("#remember-renamed-voice").addEventListener(');
  vm.runInContext(source.slice(start, source.indexOf('  rememberVoiceDialog.addEventListener("click"', start)), state);
  function click() {
    const event = { currentTarget: button };
    const pending = button.click(event);
    event.currentTarget = null; // DOM clears currentTarget when dispatch returns.
    return pending;
  }
  return { state, button, dialog, requests, messages, click };
}

test('saving two voices restores the button after currentTarget is cleared', async () => {
  const { state, button, dialog, requests, messages, click } = workspace();
  for (const speaker of state.lastDetail.speakers) {
    state.offerToRememberRenamedVoice(speaker);
    assert.equal(button.disabled, false);
    const pending = click();
    assert.equal(button.disabled, true);
    assert.equal(button.textContent, '正在儲存聲紋…');
    assert.equal(dialog.open, true);
    await click(); // Ignore duplicate submission while saving.
    await pending;
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, '記住此聲紋');
    assert.equal(dialog.open, false);
    assert.equal(state.pendingRememberSpeakerId, null);
  }
  assert.equal(requests.length, 2);
  assert.match(requests[1], /speakers\/2\/remember$/);
  assert.equal(messages.filter(item => item.type === undefined).length, 2);
});

test('failed save keeps the dialog available for retry', async () => {
  const { state, button, dialog, messages, click } = workspace({ failSave: true });
  await click();
  assert.equal(button.disabled, false);
  assert.equal(dialog.open, true);
  assert.equal(state.pendingRememberSpeakerId, 1);
  assert.equal(messages[0].message, '儲存失敗');
  assert.equal(messages[0].type, 'error');
});

test('refresh failure after successful save is identified as saved', async () => {
  const { button, dialog, requests, messages, click } = workspace({ failRefresh: true });
  await click();
  assert.equal(requests.length, 1);
  assert.equal(button.disabled, false);
  assert.equal(dialog.open, false);
  assert.match(messages[1].message, /聲紋已儲存，但畫面更新失敗/);
});
