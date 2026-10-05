const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/local_meeting_ai/web/static/js/transcript.js', 'utf8');
const open = source.slice(source.indexOf('  function openStartDialog()'),
  source.indexOf('  async function submitTranscription('));

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
