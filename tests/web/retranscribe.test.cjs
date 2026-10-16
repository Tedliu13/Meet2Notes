const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
const code = source.slice(source.indexOf('  let retranscribePending = false;'),
  source.indexOf('  function applySearch()'));
function workspace() {
  const button = { disabled: false, hidden: false, classList: {
    toggle(name, value) { button.hidden = value; },
  } };
  const state = {
    meetingId: 42, hosted: true, activeJob: null, activeTranscriptionId: 1,
    versions: [{ id: 1 }], recordings: [{ id: 8, role: 'original' }],
    document: { body: { dataset: { canManage: 'true' } }, querySelector: () => button },
    window: { confirm: () => true }, audioWasDeleted: () => false,
    t: key => key, notifications: [], requests: [], selected: [],
    toast: message => state.notifications.push(message), renderProgress() {},
    selectTranscription: async id => state.selected.push(id),
    api: async (url, options) => {
      state.requests.push({ url, options });
      return { transcription: { id: 2 }, job: { uuid: 'new', status: 'queued' } };
    },
  };
  vm.createContext(state);
  vm.runInContext(code, state);
  return { state, button };
}
test('retry posts once to the same meeting and selects a new version', async () => {
  const { state, button } = workspace();
  await Promise.all([state.retranscribeMeeting(), state.retranscribeMeeting()]);
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].url, '/api/meetings/42/retranscribe');
  assert.equal(state.requests[0].options.method, 'POST');
  assert.deepEqual(state.selected, [2]);
  assert.equal(state.versions[1].id, 1);
  assert.equal(button.disabled, true);
  state.activeJob = null;
  state.syncRetranscribeAction();
  assert.equal(button.disabled, false);
});
test('viewers and cancelled confirmations cannot submit', async () => {
  const { state } = workspace();
  state.document.body.dataset.canManage = 'false';
  await state.retranscribeMeeting();
  state.document.body.dataset.canManage = 'true';
  state.window.confirm = () => false;
  await state.retranscribeMeeting();
  assert.equal(state.requests.length, 0);
});
test('missing original hides action; a rejected request permits retry', async () => {
  const { state, button } = workspace();
  state.recordings = [];
  state.syncRetranscribeAction();
  assert.equal(button.hidden, true);
  state.api = async () => { throw new Error('active jobs'); };
  await state.retranscribeMeeting();
  assert.equal(button.disabled, false);
  assert.equal(state.notifications[0], 'active jobs');
  assert.equal(state.activeTranscriptionId, 1);
});
test('refresh failure after acceptance tells user the job already started', async () => {
  const { state, button } = workspace();
  state.selectTranscription = async () => { throw new Error('offline'); };
  await state.retranscribeMeeting();
  assert.equal(state.requests.length, 1);
  assert.equal(button.disabled, true);
  assert.equal(state.notifications.at(-1), 'transcript.retry_refresh_failed');
});
