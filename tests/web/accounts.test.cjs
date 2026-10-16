const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/local_meeting_ai/web/static/js/accounts.js', 'utf8');

function workspace(api) {
  const title = { disabled: false };
  const editor = { readOnly: false, dataset: {} };
  const state = {
    generation: 0, targetId: null, access: null,
    people: { innerHTML: '', textContent: '' }, dialog: { open: false }, api,
    escapeHTML: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    document: {
      body: { dataset: {} },
      querySelector: () => title,
      querySelectorAll(selector) {
        if (selector === '[data-permission-read-only]') return editor.dataset.permissionReadOnly ? [editor] : [];
        return [editor];
      },
    },
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('  async function loadAccess('), source.indexOf('  if (people) {', source.indexOf('  function applyReadOnly('))), state);
  return { state, title, editor };
}

test('viewers see escaped attribution and read-only editors while keeping the meeting title', async () => {
  const { state, title, editor } = workspace(async () => ({
    creator: { id: 1, name: '<owner>' }, managers: [{ id: 2, name: '<manager>' }],
    can_manage: false, can_share: false,
  }));
  await state.loadAccess(10);
  assert.equal(state.document.body.dataset.canManage, 'false');
  assert.equal(title.disabled, true);
  assert.equal(editor.readOnly, true);
  assert.match(state.people.innerHTML, /&lt;owner&gt;/);
  assert.match(state.people.innerHTML, /&lt;manager&gt;/);
  assert.doesNotMatch(state.people.innerHTML, /id="manage-meeting-people"/);
});

test('co-managers can edit but only creators and admins can change the manager list', async () => {
  let canShare = false;
  const { state, title, editor } = workspace(async () => ({ creator: { id: 1, name: 'Owner' }, managers: [], can_manage: true, can_share: canShare }));
  editor.readOnly = true;
  editor.dataset.permissionReadOnly = 'true';
  await state.loadAccess(10);
  assert.equal(title.disabled, false);
  assert.equal(editor.readOnly, false);
  assert.doesNotMatch(state.people.innerHTML, /id="manage-meeting-people"/);
  canShare = true;
  await state.loadAccess(10);
  assert.match(state.people.innerHTML, /id="manage-meeting-people"/);
});

test('late access responses from another meeting cannot grant editing or replace attribution', async () => {
  const resolvers = {};
  const { state, title } = workspace(path => new Promise(resolve => { resolvers[path] = resolve; }));
  const first = state.loadAccess(1);
  const second = state.loadAccess(2);
  resolvers['/api/meetings/2/access']({ creator: { name: 'Current' }, managers: [], can_manage: false, can_share: false });
  await second;
  resolvers['/api/meetings/1/access']({ creator: { name: 'Old' }, managers: [], can_manage: true, can_share: true });
  await first;
  assert.equal(title.disabled, true);
  assert.equal(state.targetId, 2);
  assert.match(state.people.innerHTML, /Current/);
  assert.doesNotMatch(state.people.innerHTML, /Old/);
});

test('access lookup failures keep mutation controls unavailable', async () => {
  const { state, title } = workspace(async () => { throw Error('offline'); });
  await assert.rejects(state.loadAccess(1), /offline/);
  assert.equal(state.document.body.dataset.canManage, 'false');
  assert.equal(title.disabled, true);
});

test('viewers can open an existing speaker summary without starting an AI job', async () => {
  const transcript = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
  const viewed = [], calls = [];
  const state = {
    lastDetail: { speakers: [{ id: 3, summary_status: 'completed', summary_markdown: 'Existing summary' }] },
    speakerSummaryDismissed: true,
    showSpeakerSummary: speaker => viewed.push(speaker),
    api: (...args) => calls.push(args),
  };
  vm.createContext(state);
  vm.runInContext(transcript.slice(transcript.indexOf('  async function startSpeakerSummary('), transcript.indexOf('  async function rememberSpeakerVoice(')), state);
  await state.startSpeakerSummary(3);
  assert.equal(viewed.length, 1);
  assert.equal(calls.length, 0);
  const css = fs.readFileSync('src/local_meeting_ai/web/static/css/accounts.css', 'utf8');
  assert.match(css, /\[data-summarize-speaker\]:not\(\[data-summary-ready="true"\]\)/);
});
