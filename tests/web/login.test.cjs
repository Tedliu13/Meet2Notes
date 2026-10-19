const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/local_meeting_ai/web/static/js/login.js', 'utf8');

function page(fetch) {
  const nodes = Object.fromEntries(['account-form', 'account-submit', 'account-error', 'account-password', 'account-name', 'account-username', 'choose-login', 'choose-register', 'name-field', 'register-help'].map(id => [id, {
    value: '', disabled: false, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; }, addEventListener(type, callback) { this[type] = callback; },
  }]));
  const destinations = [];
  vm.runInNewContext(source, {
    document: { querySelector: selector => nodes[selector.slice(1)], querySelectorAll: () => [nodes['choose-login'], nodes['choose-register']] },
    fetch, location: { assign: path => destinations.push(path) },
  });
  return { nodes, destinations, submit: () => nodes['account-form'].submit({ preventDefault() {} }) };
}

test('registration sends name, username and password then opens the meeting list', async () => {
  const calls = [];
  const { nodes, submit, destinations } = page(async (path, options) => { calls.push([path, options]); return { ok: true, json: async () => ({ id: 2 }) }; });
  nodes['choose-register'].click();
  assert.equal(nodes['account-name'].required, true);
  assert.equal(nodes['account-password'].minLength, 12);
  nodes['account-username'].value = ' alice ';
  nodes['account-name'].value = ' Alice ';
  nodes['account-password'].value = 'a-test-password-123';
  const pending = submit();
  await submit();
  await pending;
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/auth/register');
  assert.deepEqual(JSON.parse(calls[0][1].body), { username: 'alice', name: 'Alice', password: 'a-test-password-123' });
  assert.deepEqual(destinations, ['/meetings']);
  assert.equal(nodes['account-password'].value, '');
});

test('successful login opens the meeting list', async () => {
  const { submit, destinations } = page(async () => ({ ok: true, json: async () => ({ id: 1 }) }));
  await submit();
  assert.deepEqual(destinations, ['/meetings']);
});

test('login errors stay visible and allow retry without claiming success', async () => {
  const { nodes, submit, destinations } = page(async () => ({ ok: false, json: async () => ({ detail: 'Account disabled' }) }));
  nodes['choose-login'].click();
  await submit();
  assert.equal(nodes['account-error'].textContent, 'Account disabled');
  assert.equal(nodes['account-submit'].disabled, false);
  assert.deepEqual(destinations, []);
});
