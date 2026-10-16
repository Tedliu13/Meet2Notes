const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/known-speakers.js', 'utf8');

function widget(api) {
  const roots = {};
  for (const id of ['known-speaker-picker', 'import-known-speaker-picker', 'rebuild-known-speaker-picker']) {
    const root = { hidden: false, checked: [], status: { textContent: '' }, retry: { hidden: true } };
    root.list = { get innerHTML() { return this.html || ''; }, set innerHTML(value) { this.html = value; root.checked = []; } };
    root.querySelector = selector => selector.includes('-list') ? root.list : selector.includes('-status') ? root.status : root.retry;
    root.querySelectorAll = () => root.checked;
    roots[id] = root;
  }
  const context = {
    window: { Meet2Notes: { api, t: key => key, escapeHTML: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;') } },
    document: { getElementById: id => roots[id] },
  };
  vm.runInNewContext(source, context);
  return { picker: context.window.Meet2Notes.knownSpeakers, roots, root: roots['known-speaker-picker'] };
}

test('known speakers show only saved voice samples and escape profile names', async () => {
  const { picker, root } = widget(async () => [{ id: 1, name: '<person>', sample_path: 'voice.wav' }, { id: 2, name: 'No voice', sample_path: null }]);
  await picker.open({ enabled: true });
  assert.match(root.list.innerHTML, /&lt;person&gt;/);
  assert.doesNotMatch(root.list.innerHTML, /No voice/);
  assert.equal(root.status.textContent, 'known_speakers.optional');
  root.checked = [{ value: '1' }];
  assert.deepEqual(Array.from(picker.selectedIds()), [1]);
  assert.deepEqual(Array.from(picker.selectedIds('import-known-speaker-picker')), []);
});

test('empty profiles and globally disabled recognition have clear states', async () => {
  let calls = 0;
  const { picker, root } = widget(async () => { calls++; return []; });
  await picker.open({ enabled: true });
  assert.equal(root.status.textContent, 'known_speakers.empty');
  await picker.open({ enabled: false });
  assert.equal(root.status.textContent, 'known_speakers.disabled');
  assert.equal(calls, 1);
});

test('loading errors allow retry and the generic import reads the global toggle', async () => {
  let fail = true;
  const { picker, root } = widget(async path => {
    if (path === '/api/workspace/preferences') return { diarization: { recognize_saved_speakers: true } };
    if (fail) throw Error('offline');
    return [{ id: 3, name: 'Person', sample_path: 'saved.wav' }];
  });
  await picker.open();
  assert.equal(root.status.textContent, 'known_speakers.error');
  assert.equal(root.retry.hidden, false);
  fail = false;
  await root.retry.onclick();
  assert.match(root.list.innerHTML, /value="3"/);
  assert.equal(root.retry.hidden, true);
});

test('a late response cannot restore an old roster or show a hidden picker', async () => {
  const pending = [];
  const { picker, root } = widget(() => new Promise(resolve => pending.push(resolve)));
  const first = picker.open({ enabled: true });
  const second = picker.open({ enabled: true });
  pending[1]([{ id: 2, name: 'New', sample_path: 'new.wav' }]);
  await second;
  pending[0]([{ id: 1, name: 'Old', sample_path: 'old.wav' }]);
  await first;
  assert.match(root.list.innerHTML, /New/);
  assert.doesNotMatch(root.list.innerHTML, /Old/);
  await picker.open({ show: false });
  assert.equal(root.hidden, true);
  assert.deepEqual(Array.from(picker.selectedIds()), []);
});

test('new file transcription sends selected profile IDs when creating the meeting', async () => {
  const transcript = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
  const calls = [];
  const state = {
    draftTitle: 'New meeting', Meet2Notes: { knownSpeakers: { selectedIds: () => [4, 8] } },
    document: { querySelector: () => ({ files: [{ name: 'video.mp4' }] }) },
    async api(path, options) {
      calls.push([path, JSON.parse(options.body)]);
      return path === '/api/meetings' ? { id: 12 } : { transcription: { id: 3, title: 'New meeting' }, job: { uuid: 'job' } };
    },
    async uploadFile() {}, transcriptionOptions: () => ({}), page: { dataset: {} },
    syncStartAction() {}, setTitle() {}, openPostprocessDialog() {}, async loadMeetingWorkspace() {}, toast() {},
    window: { history: { replaceState() {} } },
  };
  vm.createContext(state);
  vm.runInContext(transcript.slice(transcript.indexOf('  async function startFileTranscription('), transcript.indexOf('  function uploadFile(')), state);
  await state.startFileTranscription({ diarization: true, speaker_count: null });
  assert.deepEqual(calls[0][1].known_speaker_profile_ids, [4, 8]);
  assert.equal(calls[1][1].postprocess_options.speaker_count, null);
});

test('rebuild preselects its roster and retains unavailable IDs visibly instead of expanding scope', async () => {
  const { picker, roots } = widget(async () => [{ id: 3, name: 'Known', sample_path: 'voice.wav' }]);
  const id = 'rebuild-known-speaker-picker';
  await picker.open({ id, enabled: true, selected: [3, 99] });
  assert.equal(picker.isReady(id), true);
  assert.equal(picker.isEnabled(id), true);
  assert.match(roots[id].list.innerHTML, /value="3" checked/);
  assert.match(roots[id].list.innerHTML, /value="99" checked/);
  assert.match(roots[id].list.innerHTML, /known_speakers.unavailable/);
});

test('rebuild cannot proceed while loading or after a failed roster request', async () => {
  let reject;
  const { picker } = widget(() => new Promise((resolve, fail) => { reject = fail; }));
  const id = 'rebuild-known-speaker-picker';
  const loading = picker.open({ id, enabled: true, selected: [3] });
  assert.equal(picker.isReady(id), false);
  reject(Error('offline'));
  await loading;
  assert.equal(picker.isReady(id), false);
});

test('rebuild submits selected voices independently of the requested speaker count', async () => {
  const transcript = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
  const calls = [];
  let ready = true, enabled = true;
  const state = {
    Meet2Notes: { knownSpeakers: {
      isReady: () => ready, isEnabled: () => enabled, selectedIds: () => [4, 8],
    } },
    document: { querySelector: selector => ({ value: selector.includes(':checked') ? 'known' : '7' }) },
    activeTranscriptionId: 14, activeSpeakerRebuildJobId: null,
    renderSpeakerPanel() {}, renderSpeakerRebuildJob() {}, pollSpeakerRebuild() {},
    rememberSpeakerJob: job => job, t: key => key, toast() {},
    async api(path, options) { calls.push(JSON.parse(options.body)); return { uuid: 'job', status: 'queued' }; },
  };
  vm.createContext(state);
  vm.runInContext(transcript.slice(transcript.indexOf('  async function startSpeakerRebuild('),
    transcript.indexOf('  function setActiveMeetingTab(')), state);
  await state.startSpeakerRebuild();
  assert.deepEqual(calls[0], { speaker_count: 7, known_speaker_profile_ids: [4, 8] });
  ready = false;
  await state.startSpeakerRebuild();
  assert.equal(calls.length, 1);
  ready = true;
  enabled = false;
  await state.startSpeakerRebuild();
  assert.deepEqual(calls[1], { speaker_count: 7 });
});
