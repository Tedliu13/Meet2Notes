const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../../src/local_meeting_ai/web/static');
const source = fs.readFileSync(path.join(web, 'js/app.js'), 'utf8');
const catalogCode = source.slice(source.indexOf('  const defaultLanguage ='), source.indexOf('  async function api('));
const controlCode = source.slice(source.indexOf('  function applyLanguage('), source.indexOf('  function resolveTheme('));

function interfaceState() {
  const title = { dataset: { i18n: 'nav.meetings' }, textContent: '' };
  const select = { dataset: {}, innerHTML: '', value: '' };
  const context = {
    document: {
      title: 'Meet2Notes', documentElement: { lang: 'en' }, body: { querySelectorAll: () => [] },
      querySelectorAll: selector => selector === '[data-i18n]' ? [title]
        : selector === '[data-ui-language]' ? [select] : [],
      createTreeWalker: () => ({ nextNode: () => false }),
      dispatchEvent() {},
    },
    NodeFilter: { SHOW_TEXT: 4 },
    CustomEvent: class { constructor(name, options) { this.type = name; this.detail = options.detail; } },
    XMLHttpRequest: class {
      open(method, url) { this.file = path.join(web, url.replace('/static/', '')); }
      send() { this.responseText = fs.readFileSync(this.file, 'utf8'); this.status = 200; }
    },
    escapeHTML: value => value,
  };
  vm.createContext(context);
  vm.runInContext('let currentLanguage = "en";\n' + catalogCode + controlCode, context);
  return { context, title, select };
}

test('Traditional Chinese renders text, interpolation and plural counts', () => {
  const { context, title, select } = interfaceState();
  context.applyLanguage('zh-TW');
  assert.equal(context.document.documentElement.lang, 'zh-TW');
  assert.equal(select.value, 'zh-TW');
  assert.equal(title.textContent, '會議');
  assert.equal(context.t('assistant.reading_context_progress', { processed: 3, total: 10 }),
    '正在處理上下文：3 / 10 個 token…');
  for (const count of [0, 1, 2]) {
    assert.equal(context.t('post_assistant.sources', { count }), `已檢索 ${count} 個來源`);
  }
  assert.equal(context.translateLiteral('Drop a file here or browse'), '將檔案拖放至此，或瀏覽檔案');
  assert.equal(context.t('nav.prompt'), 'Prompt');
});

test('language selector includes Traditional Chinese and can switch back to existing languages', () => {
  const { context, title, select } = interfaceState();
  context.populateLanguageControls();
  assert.match(select.innerHTML, /value="zh-TW">繁體中文/);
  context.applyLanguage('zh-TW');
  context.applyLanguage('es');
  assert.equal(title.textContent, 'Reuniones');
  context.applyLanguage('en');
  assert.equal(title.textContent, 'Meetings');
});
