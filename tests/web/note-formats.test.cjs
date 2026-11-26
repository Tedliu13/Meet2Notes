const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/note-formats.js', 'utf8');

class Element {
  constructor() { this.children = []; this.handlers = {}; this.dataset = {}; this.nodes = {}; this.value = ''; }
  addEventListener(type, callback) { this.handlers[type] = callback; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelector(selector) { return this.nodes[selector] ||= new Element(); }
  reset() { Object.values(this.elements).forEach(item => { item.value = ''; }); }
}
function setup(api, page = false) {
  const nodes = Object.fromEntries(['nf-editor', 'nf-form', 'nf-sections', 'nf-error', 'nf-add-section', 'nf-editor-title', 'nf-load-status', 'nf-personal', 'nf-shared'].map(id => [id, new Element()]));
  nodes['nf-form'].elements = Object.fromEntries(['name', 'description', 'system_prompt', 'user_prompt_template'].map(key => [key, new Element()]));
  const create = new Element(); create.dataset.newNoteFormat = 'ai-rebuild-format';
  const pageNode = new Element(); pageNode.dataset.userId = '2';
  const events = [];
  const document = {
    body: { dataset: { isAdmin: 'false' } },
    querySelector: selector => selector === '.nf-page' ? (page ? pageNode : null) : nodes[selector.slice(1)],
    querySelectorAll: selector => selector === '[data-new-note-format]' ? [create] : [],
    createElement: () => new Element(), getElementById: id => nodes[id],
    addEventListener() {}, dispatchEvent: event => events.push(event),
  };
  vm.runInNewContext(source, {
    document, CustomEvent: class { constructor(type, value) { this.type = type; this.detail = value.detail; } },
    window: { confirm: () => true, Meet2Notes: { api, t: key => key, toast() {},
      escapeHTML: text => String(text).replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'),
    } },
  });
  return { nodes, create, events };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('quick creation saves a personal format and returns it to the original selector', async () => {
  const calls = [];
  const { nodes, create, events } = setup(async (url, options) => { calls.push([url, JSON.parse(options.body)]); return { id: 12 }; });
  create.handlers.click();
  assert.equal(nodes['nf-editor'].open, true);
  nodes['nf-form'].elements.name.value = 'My notes';
  const row = nodes['nf-sections'].children[0];
  for (const key of ['title', 'instruction']) row.querySelector(`[data-nf="${key}"]`).value = key;
  await nodes['nf-form'].handlers.submit({ preventDefault() {} });
  assert.equal(calls[0][0], '/api/summary-templates');
  assert.equal(calls[0][1].scope, 'personal');
  assert.equal(calls[0][1].sections.length, 1);
  assert.equal(events[0].detail.id, 12);
  assert.equal(events[0].detail.selectId, 'ai-rebuild-format');
  assert.equal(nodes['nf-editor'].open, false);
});

test('save errors keep the editor and draft open for retry', async () => {
  const { nodes, create, events } = setup(async () => { throw new Error('Save failed'); });
  create.handlers.click();
  nodes['nf-form'].elements.name.value = 'Keep this';
  await nodes['nf-form'].handlers.submit({ preventDefault() {} });
  assert.equal(nodes['nf-editor'].open, true);
  assert.equal(nodes['nf-form'].elements.name.value, 'Keep this');
  assert.equal(nodes['nf-error'].textContent, 'Save failed');
  assert.equal(events.length, 0);
  assert.equal(nodes['nf-form'].querySelector('[type="submit"]').disabled, false);
});

test('shared formats can be copied but cannot be edited by a normal user', async () => {
  const item = { id: 1, name: '<shared>', scope: 'shared', sections: [], can_edit: false };
  const { nodes } = setup(async () => [item], true);
  await tick();
  const card = nodes['nf-shared'].children[0];
  assert.match(card.innerHTML, /&lt;shared&gt;/);
  const actions = card.children[0].children;
  assert.deepEqual(actions.map(item => item.textContent), ['nf.set_default', 'nf.duplicate']);
  await actions[1].handlers.click();
  assert.equal(nodes['nf-form'].elements.name.value, '<shared> nf.copy_suffix');
  assert.equal(nodes['nf-editor'].open, true);
});
