const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');

function workspace(status = 'running') {
  const nodes = new Map(), timers = [], notices = [];
  const job = { uuid: 'rebuild', status, progress: 0, message: 'Starting' };
  const state = {
    activeSpeakerRebuildJobId: job.uuid, activeTranscriptionId: 14,
    speakerRebuildPollTimer: null, speakerRebuildRefreshPromise: null,
    speakerRebuildDismissed: false, speakerRebuildDialog: { open: true, showModal() {} },
    t: key => key, isIndeterminateJob: () => false,
    document: { querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, { hidden: false, value: 0, textContent: '',
        removeAttribute() { this.value = undefined; } });
      return nodes.get(selector);
    } },
    async api() { return job; }, async selectTranscription() { return true; }, renderSpeakerPanel() {},
    toast: message => notices.push(message), console: { warn() {} },
    setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {},
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('  function renderSpeakerRebuildJob('),
    source.indexOf('  async function startSpeakerRebuild(')), state);
  return { state, job, nodes, timers, notices };
}

test('Starting and queued jobs show waiting rather than a misleading zero percent', () => {
  const { state, job, nodes } = workspace();
  state.renderSpeakerRebuildJob(job);
  assert.equal(nodes.get('#speaker-rebuild-progress').value, undefined);
  assert.equal(nodes.get('#speaker-rebuild-status').textContent, 'speaker.rebuild_starting');
  assert.equal(nodes.get('#speaker-rebuild-done').hidden, true);
  job.status = 'queued';
  state.renderSpeakerRebuildJob(job);
  assert.equal(nodes.get('#speaker-rebuild-status').textContent, 'speaker.rebuild_queued');
});

test('single-job polling completes without any SSE event and retries a failed refresh', async () => {
  const { state, job, timers, notices, nodes } = workspace('completed');
  let refreshes = 0;
  state.selectTranscription = async () => { if (++refreshes === 1) throw Error('temporary disconnect'); return true; };
  state.pollSpeakerRebuild(job.uuid);
  await timers[0]();
  assert.equal(state.activeSpeakerRebuildJobId, job.uuid);
  assert.equal(timers.length, 2);
  await timers[1]();
  assert.equal(state.activeSpeakerRebuildJobId, null);
  assert.equal(refreshes, 2);
  assert.equal(notices.length, 1);
  assert.equal(nodes.get('#speaker-rebuild-done').hidden, false);
  assert.equal(nodes.get('#speaker-rebuild-background').hidden, true);
  assert.equal(nodes.get('#speaker-rebuild-identification').disabled, false);
  assert.equal(timers.length, 2);
});

test('a superseded result refresh keeps polling and does not claim the old results are updated', async () => {
  const { state, job, timers, notices, nodes } = workspace('completed');
  state.selectTranscription = async () => false;
  state.pollSpeakerRebuild(job.uuid);
  await timers[0]();
  assert.equal(state.activeSpeakerRebuildJobId, job.uuid);
  assert.equal(timers.length, 2);
  assert.equal(notices.length, 0);
  assert.equal(nodes.get('#speaker-rebuild-description').textContent, 'transcript.loading');
  state.selectTranscription = async () => true;
  await timers[1]();
  assert.equal(state.activeSpeakerRebuildJobId, null);
  assert.equal(notices.length, 1);
});

test('a failed job exposes its error and allows another rebuild', async () => {
  const { state, job, timers, nodes } = workspace('failed');
  job.error_text = 'engine unavailable';
  state.pollSpeakerRebuild(job.uuid);
  await timers[0]();
  assert.equal(nodes.get('#speaker-rebuild-status').textContent, 'engine unavailable');
  assert.equal(state.activeSpeakerRebuildJobId, null);
  assert.equal(nodes.get('#speaker-rebuild-identification').disabled, false);
  assert.equal(timers.length, 1);
});

test('completion reports actual six speakers even when ten were requested', async () => {
  const { state, job, nodes } = workspace('completed');
  job.payload = { speaker_count: 10 };
  job.result = { speaker_count: 6 };
  state.t = (key, values) => key === 'speaker.rebuild_counts'
    ? `requested ${values.requested}, detected ${values.detected}` : key;
  await state.updateSpeakerRebuildJob(job);
  assert.equal(nodes.get('#speaker-rebuild-description').textContent, 'requested 10, detected 6');
});

test('real transcript reader propagates errors for rebuild retry instead of reporting success', async () => {
  const { state, job, timers, notices } = workspace('completed');
  Object.assign(state, {
    transcriptRequestGeneration: 0, transcriptRenderGeneration: 0, pendingSearchSegment: null,
    segmentContainer: { setAttribute() {}, removeAttribute() {} },
    renderTranscript: async () => {},
  });
  vm.runInContext(source.slice(source.indexOf('  async function selectTranscription('),
    source.indexOf('  async function renderTranscript(')), state);
  let reads = 0;
  state.api = async url => {
    if (url.startsWith('/api/jobs/')) return job;
    if (++reads === 1) throw Error('temporary disconnect');
    return { segments: [], speakers: [] };
  };
  state.pollSpeakerRebuild(job.uuid);
  await timers[0]();
  assert.equal(state.activeSpeakerRebuildJobId, job.uuid);
  assert.equal(notices.length, 0);
  await timers[1]();
  assert.equal(state.activeSpeakerRebuildJobId, null);
  assert.equal(reads, 2);
  assert.equal(notices.length, 1);
});
