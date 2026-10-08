const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');

function workspace({ profiles = [{ id: 5, name: '<person>', sample_path: '/saved.wav' }], fail = false } = {}) {
  const nodes = {};
  for (const id of ['link-person-status', 'link-person-profile', 'link-person-submit', 'link-person-form']) {
    nodes['#' + id] = { value: '', innerHTML: '', disabled: false, textContent: '',
      addEventListener(type, callback) { this[type] = callback; } };
  }
  const dialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; }, addEventListener() {} };
  const calls = [], messages = [];
  const state = {
    lastDetail: { speakers: [{ id: 12, profile_id: null }] }, activeTranscriptionId: 4,
    pendingLinkPerson: null, linkPersonDialog: dialog,
    document: { querySelector: selector => nodes[selector], querySelectorAll: () => [] },
    t: key => key, escapeHTML: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    toast: (message, type) => messages.push({ message, type }),
    async api(path, options) { calls.push({ path, options }); if (!options) return profiles;
      if (fail) throw Error('link failed'); return {}; },
    async selectTranscription() {},
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('  async function openLinkPerson('),
    source.indexOf('  function isGeneratedSpeakerName(')), state);
  const start = source.indexOf('  document.querySelectorAll("[data-close-link-person]")');
  vm.runInContext(source.slice(start, source.indexOf('  document.querySelectorAll("[data-close-remember-voice]")', start)), state);
  return { state, nodes, dialog, calls, messages,
    submit: () => nodes['#link-person-form'].submit({ preventDefault() {} }) };
}

test('links the selected identity and escapes saved names', async () => {
  const { state, nodes, dialog, calls, messages, submit } = workspace();
  await state.openLinkPerson(12);
  assert.equal(dialog.open, true);
  assert.match(nodes['#link-person-profile'].innerHTML, /&lt;person&gt;/);
  nodes['#link-person-profile'].value = '5';
  const pending = submit();
  await submit(); // Ignore duplicate submission.
  await pending;
  assert.equal(calls.length, 2);
  assert.equal(calls[1].path, '/api/transcriptions/4/speakers/12/profile');
  assert.deepEqual(JSON.parse(calls[1].options.body), { profile_id: 5 });
  assert.equal(dialog.open, false);
  assert.equal(messages[0].message, 'voice.link_success');
});

test('failed assignment keeps the selected person and enables retry', async () => {
  const { state, nodes, dialog, submit } = workspace({ fail: true });
  await state.openLinkPerson(12);
  nodes['#link-person-profile'].value = '5';
  await submit();
  assert.equal(dialog.open, true);
  assert.equal(nodes['#link-person-submit'].disabled, false);
  assert.equal(nodes['#link-person-status'].textContent, 'link failed');
});

test('an empty saved voice directory cannot be submitted', async () => {
  const { state, nodes, calls, submit } = workspace({ profiles: [] });
  await state.openLinkPerson(12);
  assert.equal(nodes['#link-person-submit'].disabled, true);
  assert.equal(nodes['#link-person-status'].textContent, 'voice.link_empty');
  await submit();
  assert.equal(calls.length, 1);
});
