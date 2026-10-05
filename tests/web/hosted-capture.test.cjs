const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
const open = source.slice(source.indexOf('  function openStartDialog()'),
  source.indexOf('  async function submitTranscription('));

function filePicker() {
  const elements = {};
  for (const id of ['capture-file', 'capture-file-drop', 'capture-file-name', 'capture-file-help', 'transcription-form']) {
    const classes = new Set();
    elements[id] = {
      files: [], dataset: {}, listeners: {}, textContent: '',
      classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
      addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
      dispatchEvent(event) {
        event.target = this;
        for (const callback of this.listeners[event.type] || []) callback(event);
      },
    };
  }
  const start = source.indexOf('  document.querySelector("#capture-file").addEventListener("change"');
  const end = source.indexOf('  document.querySelector("#pause-capture")', start);
  vm.runInNewContext(source.slice(start, end), {
    document: { querySelector: selector => elements[selector.slice(1)] },
    formatBytes: size => `${size} bytes`, resetFilePicker() {},
    Event: class { constructor(type) { this.type = type; } },
    DataTransfer: class {
      constructor() { this.files = []; this.items = { add: file => this.files.push(file) }; }
    },
  });
  return elements;
}

test('dropping media selects the file used by import and displays its name and size', () => {
  const elements = filePicker();
  const zone = elements['capture-file-drop'];
  const file = { name: 'meeting.mp3', size: 4096 };
  let prevented = 0;
  zone.dispatchEvent({ type: 'dragover', preventDefault() { prevented++; } });
  assert.equal(zone.classList.contains('dragging'), true);
  zone.dispatchEvent({ type: 'drop', dataTransfer: { files: [file] }, preventDefault() { prevented++; } });
  assert.equal(prevented, 2);
  assert.equal(elements['capture-file'].files[0], file);
  assert.equal(elements['capture-file-name'].textContent, file.name);
  assert.equal(elements['capture-file-help'].textContent, '4096 bytes');
  assert.equal(zone.classList.contains('has-file'), true);
  assert.equal(zone.classList.contains('dragging'), false);
});

test('Browse still uses the same selected file display', () => {
  const elements = filePicker();
  const file = { name: 'meeting.wav', size: 1024 };
  elements['capture-file'].files = [file];
  elements['capture-file'].dispatchEvent({ type: 'change' });
  assert.equal(elements['capture-file-name'].textContent, file.name);
  assert.equal(elements['capture-file-help'].textContent, '1024 bytes');
});

test('empty drops and drops during upload preserve the selected file', () => {
  const elements = filePicker();
  const input = elements['capture-file'];
  const original = { name: 'original.mp3' };
  input.files = [original];
  const drop = files => elements['capture-file-drop'].dispatchEvent({ type: 'drop', dataTransfer: { files }, preventDefault() {} });
  drop([]);
  assert.equal(input.files[0], original);
  elements['transcription-form'].dataset.busy = 'true';
  drop([{ name: 'replacement.wav' }]);
  assert.equal(input.files[0], original);
});

for (const hosted of [true, false]) {
  test(`new transcription selects ${hosted ? 'file import' : 'microphone'} in ${hosted ? 'hosted' : 'desktop'} mode`, () => {
    const inputs = ['microphone', 'system', 'file'].map(value => {
      const label = { hidden: false };
      return { value, checked: false, disabled: false, closest: () => label, label };
    });
    const context = {
      hosted,
      document: {
        querySelector: () => ({ reset() { inputs.forEach(input => { input.checked = false; }); } }),
        querySelectorAll: () => inputs,
      },
      renderSources() {}, resetFilePicker() {}, scheduleSourcePreview() {},
      startDialog: { opened: false, showModal() { this.opened = true; } },
    };
    vm.runInNewContext(open + '\nopenStartDialog();', context);
    assert.equal(inputs.find(input => input.checked).value, hosted ? 'file' : 'microphone');
    assert.equal(context.startDialog.opened, true);
    if (hosted) {
      for (const input of inputs.filter(input => input.value !== 'file')) {
        assert.equal(input.disabled, true);
        assert.equal(input.label.hidden, true);
      }
    }
  });
}
