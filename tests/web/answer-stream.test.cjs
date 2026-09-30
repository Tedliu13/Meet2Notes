const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../src/local_meeting_ai/web/static/js/answer-stream.js'), 'utf8');
function load(fetch, api = () => { throw new Error('Unexpected retry'); }) {
  const context = vm.createContext({ fetch, TextDecoder, window: { Meet2Notes: { api, t: key => key } } });
  vm.runInContext(source, context);
  return context.window.Meet2Notes.streamAnswer;
}
function response(text) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } }));
}
test('decodes split Unicode, fragmented events and final metadata', async () => {
  const events = [];
  const run = load(async () => response('{"type":"delta","text":"Acción 👋"}\n{"type":"done","result":{"answer":"Acción 👋","sources":[1]}}\n'));
  const result = await run('/api/prompt', {}, event => events.push(event));
  assert.equal(events[0].text, 'Acción 👋');
  assert.equal(result.answer, 'Acción 👋');
  assert.equal(result.sources[0], 1);
});
test('EOF and provider errors reject without fabricating success or retrying', async () => {
  for (const data of ['{"type":"delta","text":"Partial"}\n', '{"type":"error","message":"Disconnected"}\n']) {
    const run = load(async () => response(data));
    await assert.rejects(run('/api/prompt', {}, () => {}), /interrupted|Disconnected/);
  }
});
test('previous backends fall back to the existing JSON endpoint', async () => {
  let calls = 0;
  const run = load(async () => new Response('', { status: 404 }), async (url, options) => {
    calls += 1;
    assert.equal(url, '/api/prompt');
    assert.equal(JSON.parse(options.body).question, 'Hello');
    return { answer: 'Buffered' };
  });
  assert.equal((await run('/api/prompt', { question: 'Hello' }, () => {})).answer, 'Buffered');
  assert.equal(calls, 1);
});
