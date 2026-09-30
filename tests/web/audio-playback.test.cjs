const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
function workspace(recordings, src = '') {
  const nodes = new Map();
  const state = { recordings, currentMeeting: {}, stopped: 0,
    stopAudioPlayback() { state.stopped++; },
    document: { querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, { textContent: '', hidden: false,
        classList: { add() { nodes.get(selector).hidden = true; }, remove() { nodes.get(selector).hidden = false; } } });
      return nodes.get(selector);
    } },
    audio: { src, getAttribute() { return this.src; }, removeAttribute() { this.src = ''; } },
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('  function playbackRecording('), source.indexOf('  function applyAudioAvailability(')), state);
  state.applyAudioAvailability = () => {};
  return { state, nodes };
}
const original = { id: 59, role: 'original', original_filename: 'debate.mp3' };
const normalized = { id: 60, role: 'normalized', original_filename: 'transcription-source.wav' };
test('all shared playback uses PCM while retaining the recognizable original filename', () => {
  const {state,nodes} = workspace([original,normalized], '/api/recordings/59/media');
  state.configureAudio();
  assert.equal(state.audio.src, '/api/recordings/60/media');
  assert.equal(nodes.get('#audio-filename').textContent, 'debate.mp3');
  assert.equal(state.stopped, 1);
  state.audio.src += '#t=7741.31';
  state.configureAudio();
  assert.equal(state.audio.src, '/api/recordings/60/media#t=7741.31');
  assert.equal(state.stopped, 1, 'refresh must not interrupt an unchanged source');
});
test('latest normalized recording wins; original remains usable before normalization', () => {
  const {state} = workspace([original]);
  state.configureAudio();assert.equal(state.audio.src, '/api/recordings/59/media');
  state.recordings = [original,normalized,{id:61,role:'normalized'}];
  state.configureAudio();assert.equal(state.audio.src, '/api/recordings/61/media');
  state.recordings = [normalized];
  state.configureAudio();assert.equal(state.audio.src, '/api/recordings/60/media');
});
test('deleted or missing audio clears any previous source', () => {
  const {state,nodes} = workspace([original,normalized], '/api/recordings/60/media');
  state.currentMeeting.audio_deleted_at = '2026-09-30';state.configureAudio();
  assert.equal(state.audio.src, '');assert.equal(nodes.get('#audio-row').hidden,true);
  state.currentMeeting = {};state.recordings = [];state.configureAudio();
  assert.equal(state.audio.src, '');
});
