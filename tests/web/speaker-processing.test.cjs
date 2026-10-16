const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');

function workspace() {
  const nodes = new Map(), timers = [];
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: selector.endsWith('order') ? 'time' : 'all', innerHTML: '', textContent: '',
      classList: { add() {}, remove() {} }, attributes: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; }, querySelector: node,
    });
    return nodes.get(selector);
  }
  const state = {
    activeTranscriptionId: 14, activeSpeakerRebuildJobId: null,
    speakerRebuildSnapshot: null, speakerJobSnapshot: new Map(), speakerRebuildDismissed: true,
    speakerRebuildRefreshPromise: null, speakerRebuildPollTimer: null,
    speakerRebuildDialog: { open: false, showModal() { this.open = true; } },
    lastDetail: { transcription: { status: 'completed' }, segments: [], speaker_turns: [],
      speakers: [{ id: 1, display_name: 'OLD SPEAKER', talk_time_ms: 1000, segment_count: 1 }] },
    document: { querySelector: node }, t: key => key,
    escapeHTML: text => String(text).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    formatTimestamp: String, applyAudioAvailability() {}, isIndeterminateJob: () => false,
    setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {},
    toast() {}, console: { warn() {} },
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('  function rememberSpeakerJob('),
    source.indexOf('  function transcriptTextForSpeakerTurn(')), state);
  vm.runInContext(source.slice(source.indexOf('  function renderSpeakerRebuildJob('),
    source.indexOf('  async function startSpeakerRebuild(')), state);
  return { state, nodes, timers, panel: () => node('#speaker-results'),
    status: () => node('#speaker-result-status') };
}

const job = (status = 'running', transcriptionId = 14) => ({
  uuid: 'rebuild', status, progress: 0.4, message: '<Analyzing speakers>',
  payload: { transcription_id: transcriptionId },
});

test('background rebuild replaces old cards with an escaped animated progress view', () => {
  const { state, panel, status, nodes } = workspace();
  state.renderSpeakerPanel();
  assert.match(panel().innerHTML, /OLD SPEAKER/);
  state.activeSpeakerRebuildJobId = 'rebuild';
  state.renderSpeakerRebuildJob(job());
  assert.doesNotMatch(panel().innerHTML, /OLD SPEAKER|data-remember-speaker|data-link-person/);
  assert.match(panel().innerHTML, /mini-spinner/);
  assert.match(panel().innerHTML, /&lt;Analyzing speakers&gt;/);
  assert.match(panel().innerHTML, /value="40"/);
  assert.equal(panel().attributes['aria-busy'], 'true');
  assert.equal(status().textContent, 'speaker.processing');
  assert.equal(nodes.get('#speaker-rebuild-identification').disabled, true);
  state.renderSpeakerPanel(); // Switching away and back still shows progress.
  assert.doesNotMatch(panel().innerHTML, /OLD SPEAKER/);
  assert.equal(state.speakerRebuildDialog.open, false);
});

test('reopening a page restores a queued job and polls without starting another inference', () => {
  const { state, panel, timers } = workspace();
  state.rememberSpeakerJob(job('queued'));
  state.renderSpeakerPanel();
  assert.equal(state.activeSpeakerRebuildJobId, 'rebuild');
  assert.equal(timers.length, 1);
  assert.match(panel().innerHTML, /speaker.rebuild_queued/);
  assert.doesNotMatch(panel().innerHTML, /value="/);
});

test('a job for a different transcription does not hide this version', () => {
  const { state, panel } = workspace();
  state.rememberSpeakerJob(job('running', 99));
  state.renderSpeakerPanel();
  assert.match(panel().innerHTML, /OLD SPEAKER/);
  assert.equal(state.activeSpeakerRebuildJobId, null);
});

test('completion keeps old cards hidden until fresh results load successfully', async () => {
  const { state, panel, status } = workspace();
  state.activeSpeakerRebuildJobId = 'rebuild';
  let succeed = false;
  state.selectTranscription = async () => {
    if (!succeed) throw Error('temporary disconnect');
    state.lastDetail.speakers[0].display_name = 'NEW SPEAKER';
    state.renderSpeakerPanel();
    return true;
  };
  await assert.rejects(state.updateSpeakerRebuildJob(job('completed')));
  assert.equal(status().textContent, 'speaker.refreshing');
  assert.doesNotMatch(panel().innerHTML, /OLD SPEAKER/);
  succeed = true;
  await state.updateSpeakerRebuildJob(job('completed'));
  assert.equal(state.activeSpeakerRebuildJobId, null);
  assert.match(panel().innerHTML, /NEW SPEAKER/);
  assert.equal(panel().attributes['aria-busy'], undefined);
  state.rememberSpeakerJob(job('running')); // An older SSE snapshot cannot regress it.
  state.renderSpeakerPanel();
  assert.match(panel().innerHTML, /NEW SPEAKER/);
});

test('failure restores old cards labelled as previous results', async () => {
  const { state, panel, status } = workspace();
  state.activeSpeakerRebuildJobId = 'rebuild';
  await state.updateSpeakerRebuildJob(job('failed'));
  assert.match(panel().innerHTML, /OLD SPEAKER/);
  assert.equal(status().textContent, 'speaker.previous_results');
  assert.equal(state.activeSpeakerRebuildJobId, null);
});
