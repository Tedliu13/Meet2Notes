// Run with: node --test tests/web/markdown.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../../src/local_meeting_ai/web');
const app = fs.readFileSync(path.join(root, 'static/js/app.js'), 'utf8');
const escapeFunction = app.slice(app.indexOf('  function escapeHTML('), app.indexOf('  function formatDate('));
const context = vm.createContext({ window: { Meet2Notes: {} } });
vm.runInContext(`${escapeFunction}\nwindow.Meet2Notes.escapeHTML = escapeHTML;`, context);
vm.runInContext(fs.readFileSync(path.join(root, 'static/js/markdown.js'), 'utf8'), context);
const render = context.window.Meet2Notes.renderMarkdown;

test('citations resolve only provided evidence, escape titles and leave code intact', () => {
  context.window.Meet2Notes.t = (key) => key;
  const cited = context.window.Meet2Notes.renderCitedAnswer;
  const sources = [
    { citation_id: 'A1', kind: 'transcription', meeting_id: 38, meeting_title: '<Launch>' },
    { citation_id: 'R2', kind: 'excerpt', meeting_id: 39, meeting_title: 'Review', start_ms: 125000 },
  ];
  const html = cited('1. Harold [A1]\n2. PDF [R2]\n\n> Unknown [R1]\n\n`[A1]`\n\n```\n[R2]\n```', sources);
  assert.match(html, /href="\/\?meeting=38">assistant.source_transcript: &lt;Launch&gt;/);
  assert.match(html, /Review · 02:05/);
  assert.match(html, /unverified">assistant.source_unverified/);
  assert.match(html, /<code>\[A1\]<\/code>/);
  assert.match(html, /<pre><code>\[R2\]<\/code><\/pre>/);
  assert.doesNotMatch(html, /<Launch>/);
  assert.match(cited('[A1]', [{ ...sources[0], meeting_id: 'javascript:alert(1)' }]), /unverified/);
});

test('task answers preserve numbering, multiline details and citations', () => {
  const html = render('**Next steps:**\n\n1. **Task:** Redesign onboarding\n**Owner:** Victor\n**Evidence:** [R1]\n\n2. **Task:** Coordinate testers');
  assert.match(html, /<strong>Next steps:<\/strong>/);
  assert.equal((html.match(/<ol/g) || []).length, 1);
  assert.equal((html.match(/<li>/g) || []).length, 2);
  assert.match(html, /<strong>Owner:<\/strong> Victor<br><strong>Evidence:<\/strong> \[R1\]/);
  assert.match(render('3. Third\n4. Fourth'), /<ol start="3">/);
});
test('nested lists, headings, quotes, tables and unfinished fences render', () => {
  const html = render('# Decisions\n\n- Launch\n  - Friday\n  - Owner\n- Review\n\n> **Agreed**\n\n| Task | Owner |\n| --- | --- |\n| Launch | Alex |\n\n```python\nprint("ok")');
  assert.match(html, /<h1>Decisions<\/h1>/);
  assert.match(html, /<li><p>Launch<\/p><ul>/);
  assert.match(html, /<blockquote><p><strong>Agreed/);
  assert.match(html, /<th>Owner<\/th>/);
  assert.match(html, /<td>Alex<\/td>/);
  assert.match(html, /<pre><code>print\(&quot;ok&quot;\)<\/code><\/pre>/);
});
test('untrusted HTML, dangerous URLs and external images remain inert', () => {
  const html = render('<img src=x onerror=alert(1)>\n<script>alert(1)</script>\n[bad](javascript:alert) [data](data:text/html,test)\n![tracking](https://example.com/pixel)\n```\n<svg onload=alert(1)>\n```');
  assert.doesNotMatch(html, /<(?:img|script|svg)\b|href="(?:javascript|data):/i);
  assert.match(html, /&lt;img/);
  assert.match(html, /<a href="#">bad<\/a>/);
  assert.doesNotMatch(html, /https:\/\/example.com\/pixel/);
  assert.match(render('[docs](https://example.com/a_b)'), /rel="noopener noreferrer"/);
});
test('code and placeholder-like input survive without accidental substitutions', () => {
  assert.equal(render('M2NMARKDOWNTOKEN0X `**raw**`'), '<p>M2NMARKDOWNTOKEN0X <code>**raw**</code></p>');
  assert.match(render('> '.repeat(100) + '<script>x</script>'), /&lt;script&gt;/);
});
