const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
const state = () => {
  const nodes = new Map();
  const context = { activeJob: null, t: key => key, document: {
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, {
        value: 0, textContent: '', classList: { add() {}, remove() {} },
        removeAttribute(name) { if (name === 'value') this.value = undefined; },
      });
      return nodes.get(selector);
    },
  } };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function isIndeterminateJob('),
    source.indexOf('  function renderJobStep(')), context);
  vm.runInContext(source.slice(source.indexOf('  function renderProgress('),
    source.indexOf('  function formatTimestamp(')), context);
  return { context, nodes };
};

test('diarize activity is indeterminate until measurable postprocessing resumes', () => {
  const { context, nodes } = state();
  context.renderProgress({ status: 'running', progress: 0.05,
    message: 'CPU speaker analysis still running · 2m 30s elapsed · percentage unavailable' });
  assert.equal(nodes.get('#transcription-progress-bar').value, undefined);
  assert.equal(nodes.get('#transcription-progress-value').textContent, 'audio.working');
  assert.match(nodes.get('#transcription-progress-message').textContent, /2m 30s/);
  context.renderProgress({ status: 'running', progress: 0.91, message: 'Matching saved voice profiles' });
  assert.equal(nodes.get('#transcription-progress-bar').value, 91);
  assert.equal(nodes.get('#transcription-progress-value').textContent, '91%');
});
