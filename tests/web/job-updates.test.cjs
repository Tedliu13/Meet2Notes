const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../src/local_meeting_ai/web/static/js/transcript.js'), 'utf8');
const updates = source.slice(source.indexOf('  async function applyJobUpdates('), source.indexOf('  async function initializeWorkspace('));
const rebuild = source.slice(source.indexOf('  async function rebuildAiNotes('), source.indexOf('  function beginAiNotesEdit('));
const job = (uuid, job_type, extra = {}) => ({ uuid, job_type, meeting_id: 38, status: 'completed', payload: {}, ...extra });
const summary = { id: 29, transcription_id: 38, status: 'completed', content_markdown: '# Updated notes' };
function workspace() {
  const state = {
    meetingId: '38', activeTranscriptionId: 38, terminalJobIds: new Set(),
    activeSpeakerRebuildJobId: null, activeSpeakerSummaryJobId: null,
    meetingSummaries: [{ ...summary, status: 'queued', content_markdown: null }], versions: [],
    requests: [], rendered: [], selected: [], notifications: [], timers: [],
    renderPostprocess() {}, renderProgress() {},
    renderSummaryPanel() { state.rendered.push(state.meetingSummaries); },
    selectTranscription: async id => state.selected.push(id),
    toast: message => state.notifications.push(message),
    api: async url => { state.requests.push(url); return url.endsWith('/summaries') ? [summary] : [{ id: 38, is_active: true }]; },
    subscribeJobs: callback => { state.notify = callback; },
    setTimeout: callback => { state.timers.push(callback); return state.timers.length; },
    clearTimeout() {}, console: { warn() {} },
  };
  vm.createContext(state);
  vm.runInContext(updates, state);
  return state;
}
test('summary and its newer index job finishing together refresh AI notes', async () => {
  const state = workspace();
  await state.applyJobUpdates([job('index', 'index_search'), job('summary', 'summarize')]);
  assert.equal(state.meetingSummaries[0].content_markdown, '# Updated notes');
  assert.equal(state.rendered.length, 1);
  assert.deepEqual([...state.terminalJobIds], ['index', 'summary']);
  await state.applyJobUpdates([job('index', 'index_search'), job('summary', 'summarize')]);
  assert.equal(state.rendered.length, 1);
});
test('one snapshot refreshes every affected view and ignores other meetings', async () => {
  const state = workspace();
  await state.applyJobUpdates([
    job('other', 'summarize', { meeting_id: 999 }),
    job('index', 'index_search'), job('summary', 'summarize'),
    job('diarize', 'diarize'), job('transcribe', 'transcribe'),
  ]);
  assert.equal(state.requests.length, 2);
  assert.deepEqual(state.selected, [38]);
  assert.equal(state.rendered.length, 1);
  assert.equal(state.terminalJobIds.has('other'), false);
});
test('failed refresh is retried even if SSE sends no further changes', async () => {
  const state = workspace();
  const original = state.api;
  let calls = 0;
  state.api = async url => { if (++calls === 1) throw new Error('Temporary network failure'); return original(url); };
  state.notify([job('index', 'index_search'), job('summary', 'summarize')]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.terminalJobIds.size, 0);
  assert.equal(state.timers.length, 1);
  await state.timers[0]();
  assert.equal(state.rendered.length, 1);
  assert.equal(state.terminalJobIds.has('summary'), true);
});
test('overlapping snapshots do not issue concurrent refreshes or duplicate notifications', async () => {
  const state = workspace();
  const original = state.api;
  let release;
  state.api = async url => { await new Promise(resolve => { release = resolve; }); return original(url); };
  state.notify([job('summary', 'summarize')]);
  state.notify([job('index', 'index_search'), job('summary', 'summarize')]);
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.rendered.length, 1);
  assert.equal(state.terminalJobIds.size, 2);
});
test('late queued POST response does not replace a completed SSE result', async () => {
  const state = workspace();
  state.document = { querySelector: () => ({ value: '8', disabled: false }) };
  state.aiRebuildDialog = { close() {} };
  state.editingSummaryId = null;
  let finish;
  state.api = () => new Promise(resolve => { finish = resolve; });
  vm.runInContext(rebuild, state);
  const request = state.rebuildAiNotes();
  state.meetingSummaries = [summary];
  finish({ summary: { ...summary, status: 'queued', content_markdown: null } });
  await request;
  assert.equal(state.meetingSummaries.length, 1);
  assert.equal(state.meetingSummaries[0].status, 'completed');
});
