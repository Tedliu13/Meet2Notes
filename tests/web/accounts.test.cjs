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
    people: { innerHTML: '', textContent: '' }, dialog: { open: false }, creatorDialog: { open: false }, api,
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

test('administrators see both creator and co-manager edit actions on a meeting', async () => {
  const { state } = workspace(async () => ({ creator: { id: 1, name: 'Owner' }, managers: [], can_manage: true, can_share: true, can_transfer: true }));
  await state.loadAccess(10);
  assert.match(state.people.innerHTML, /id="manage-meeting-creator"/);
  assert.match(state.people.innerHTML, /id="manage-meeting-people"/);
});

function accessEditor(api, isAdmin = true) {
  const modal = () => ({ open: false, showModal() { this.open = true; } });
  const state = {
    targetId: 10, generation: 1,
    access: { creator: { id: 1 }, managers: [{ id: 2 }], can_share: true, can_transfer: isAdmin },
    creatorSelect: { innerHTML: '', value: '' }, options: { innerHTML: '' },
    creatorDialog: modal(), dialog: modal(), api,
    escapeHTML: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    document: { querySelector: () => ({ disabled: false }) },
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('    async function openAccessEditor('), source.indexOf('    document.querySelector("#close-meeting-creator")')), state);
  return state;
}

test('creator chooser escapes names, selects the current owner and is admin-only', async () => {
  let calls = 0;
  const api = async () => { calls++; return [{ id: 1, name: '<owner>' }, { id: 2, name: 'Other' }]; };
  const state = accessEditor(api);
  await state.openAccessEditor('creator');
  assert.equal(state.creatorSelect.value, '1');
  assert.match(state.creatorSelect.innerHTML, /&lt;owner&gt;/);
  assert.equal(state.creatorDialog.open, true);
  const member = accessEditor(api, false);
  await member.openAccessEditor('creator');
  assert.equal(member.creatorDialog.open, false);
  assert.equal(calls, 1);
});

test('co-manager chooser keeps existing selections and a stale directory cannot open on another meeting', async () => {
  const state = accessEditor(async () => [{ id: 1, name: 'Owner' }, { id: 2, name: 'Manager' }]);
  await state.openAccessEditor('managers');
  assert.doesNotMatch(state.options.innerHTML, /value="1"/);
  assert.match(state.options.innerHTML, /value="2" checked/);
  let resolve;
  const stale = accessEditor(() => new Promise(done => { resolve = done; }));
  const pending = stale.openAccessEditor('creator');
  stale.targetId = 20;
  resolve([{ id: 2, name: 'Other' }]);
  await pending;
  assert.equal(stale.creatorDialog.open, false);
});

function creatorSubmission(fail = false) {
  const button = { disabled: false, addEventListener(type, callback) { this[type] = callback; } };
  const calls = [], refreshes = [], messages = [];
  const state = {
    targetId: 10, creatorSelect: { value: '2' },
    creatorDialog: { open: true, close() { this.open = false; } },
    document: { querySelector: () => button },
    async api(path, options) { calls.push([path, options]); if (fail) throw Error('Transfer rejected'); },
    async loadAccess(id) { refreshes.push(id); },
    toast: (message, kind) => messages.push([message, kind]),
  };
  vm.createContext(state);
  vm.runInContext(source.slice(source.indexOf('    document.querySelector("#save-meeting-creator").addEventListener'), source.indexOf('    document.querySelector("#save-meeting-managers").addEventListener')), state);
  return { state, button, calls, refreshes, messages };
}

test('creator save posts the selected account, ignores duplicate clicks and refreshes attribution', async () => {
  const { state, button, calls, refreshes } = creatorSubmission();
  const event = { currentTarget: button };
  const pending = button.click(event);
  event.currentTarget = null;
  await button.click({ currentTarget: button });
  await pending;
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/meetings/10/creator');
  assert.deepEqual(JSON.parse(calls[0][1].body), { user_id: 2 });
  assert.deepEqual(refreshes, [10]);
  assert.equal(state.creatorDialog.open, false);
  assert.equal(button.disabled, false);
});

test('failed creator transfer keeps the dialog open and allows retry', async () => {
  const { state, button, messages, refreshes } = creatorSubmission(true);
  await button.click({ currentTarget: button });
  assert.equal(state.creatorDialog.open, true);
  assert.equal(button.disabled, false);
  assert.equal(messages[0][1], 'error');
  assert.deepEqual(refreshes, []);
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
