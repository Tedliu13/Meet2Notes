const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
const render = source.slice(source.indexOf('  async function renderTranscript('), source.indexOf('  function renderMeetingResults('));
function workspace() {
  const elements = new Map();
  const node = () => ({ value: 'all', textContent: '', classList: { toggle() {} } });
  const state = {
    transcriptRenderGeneration: 0, lastDetail: null, captureSession: null, searchSegmentId: null,
    document: { querySelector: selector => {
      if (!elements.has(selector)) elements.set(selector, node());
      return elements.get(selector);
    } },
    t: key => key, Meet2Notes: { t: key => key }, escapeHTML: text => text,
    formatTimestamp: value => value, setTitle() {}, renderMeetingResults() {},
    applySearch() {}, applyAudioAvailability() {}, batches: [], waits: [],
    segmentContainer: {
      setAttribute() {}, removeAttribute() {},
      replaceChildren() { state.batches = []; },
      insertAdjacentHTML(position, html) { state.batches.push(html); },
    },
    yieldToBrowser: () => new Promise(resolve => state.waits.push(resolve)),
  };
  vm.createContext(state);
  vm.runInContext(render, state);
  return state;
}
const detail = (count, prefix) => ({ transcription: { model: 'small', language: 'en' },
  speakers: [], segments: Array.from({ length: count }, (_, id) => ({ id, text: `${prefix}${id}`,
    speaker_id: null, start_ms: id * 1000, segment_index: id, is_final: true })) });

test('long transcript yields while rendering and replacing it cancels remaining old rows', async () => {
  const state = workspace();
  const oldRender = state.renderTranscript(detail(1247, 'OLD'));
  assert.equal(state.batches.length, 1);
  assert.equal((state.batches[0].match(/<article/g) || []).length, 40);
  const newRender = state.renderTranscript(detail(2, 'NEW'));
  state.waits.splice(0).forEach(resolve => resolve());
  await Promise.all([oldRender, newRender]);
  assert.equal(state.batches.length, 1);
  assert.match(state.batches[0], /NEW1/);
  assert.doesNotMatch(state.batches[0], /OLD/);
});
