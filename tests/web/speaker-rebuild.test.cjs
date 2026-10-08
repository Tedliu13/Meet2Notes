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
    async api() { return job; }, async selectTranscription() {}, renderSpeakerPanel() {},
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
  state.selectTranscription = async () => { if (++refreshes === 1) throw Error('temporary disconnect'); };
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
