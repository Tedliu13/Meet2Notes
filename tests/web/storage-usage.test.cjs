const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/storage-usage.js', 'utf8');
function workspace(api) {
  const elements = {};
  const document = { querySelector: key => elements[key], querySelectorAll: () => [], addEventListener() {} };
  const state = { document, URLSearchParams, window: { Meet2Notes: {
    api, escapeHTML: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    formatBytes: value => `${value} bytes`, t: (key, values = {}) => `${key} ${JSON.stringify(values)}`,
  } } };
  vm.runInNewContext(source, state);
  return { elements, usage: state.window.Meet2Notes.storageUsage };
}
const node = id => ({ dataset: { meetingUsage: String(id) } });
test('meeting sizes are fetched together and partial scans remain labelled as a lower bound', async () => {
  const calls = [];
  const { usage } = workspace(async url => { calls.push(url); return { meetings: [
    { id: 1, bytes: 100, complete: true }, { id: 2, bytes: 50, complete: false },
  ] }; });
  const nodes = [node(1), node(2)];
  await usage.refreshMeetings({ querySelectorAll: () => nodes });
  assert.match(calls[0], /meeting_ids=1&meeting_ids=2/);
  assert.match(nodes[0].textContent, /100 bytes/);
  assert.match(nodes[1].textContent, /≥ 50 bytes/);
  assert.match(nodes[0].title, /storage.meeting_help/);
});
test('a late meeting response cannot replace sizes from the latest library page', async () => {
  const pending = [];
  const { usage } = workspace(() => new Promise(resolve => pending.push(resolve)));
  const nodes = [node(1)];
  const root = { querySelectorAll: () => nodes };
  const first = usage.refreshMeetings(root);
  const second = usage.refreshMeetings(root);
  pending[1]({ meetings: [{ id: 1, bytes: 200, complete: true }] });
  await second;
  pending[0]({ meetings: [{ id: 1, bytes: 100, complete: true }] });
  await first;
  assert.match(nodes[0].textContent, /200 bytes/);
});
test('usage errors are visible and settings refresh remains available', async () => {
  const { elements, usage } = workspace(async () => { throw Error('offline'); });
  elements['#storage-usage-details'] = {};
  elements['#refresh-storage-usage'] = { disabled: false };
  await usage.refreshSettings();
  assert.match(elements['#storage-usage-details'].textContent, /storage.error/);
  assert.equal(elements['#refresh-storage-usage'].disabled, false);
  const nodes = [node(1)];
  await usage.refreshMeetings({ querySelectorAll: () => nodes });
  assert.match(nodes[0].textContent, /storage.error/);
});
test('settings escape paths and show folder sizes separately from filesystem free space', async () => {
  const { elements, usage } = workspace(async () => ({ directories: [{
    name: 'data', path: '<unsafe>', bytes: 10, files: 2, complete: true,
    scanned_at: '2026-10-21T00:00:00Z', filesystem: { total: 1000, used: 600, free: 400 },
  }], system: { path: '/', total: 1000, used: 600, free: 400 } }));
  elements['#storage-usage-details'] = {};
  elements['#refresh-storage-usage'] = { disabled: false };
  await usage.refreshSettings();
  const html = elements['#storage-usage-details'].innerHTML;
  assert.match(html, /&lt;unsafe&gt;/);
  assert.doesNotMatch(html, /<unsafe>/);
  assert.match(html, /storage.folder/);
  assert.match(html, /400 bytes/);
});
