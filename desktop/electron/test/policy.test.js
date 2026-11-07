'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { isAllowedUrl, isAllowedNavigation } = require('../src/policy');

const P = 18792;
const table = [
  ['http://127.0.0.1:18792/replays', true],
  ['http://127.0.0.1:18792/api/x?y=1', true],
  ['ws://127.0.0.1:18792/ws', true],
  ['data:text/html,hi', true],
  ['blob:http://127.0.0.1:18792/abc-123', true],
  ['http://127.0.0.1.evil.com:18792/', false],
  ['http://127.0.0.1@evil.com/', false],
  ['http://evil.com@127.0.0.1:18792/', false],
  ['http://127.0.0.1:18792@evil.com/', false],
  ['wss://127.0.0.1:18792/ws', false],
  ['https://127.0.0.1:18792/', false],
  ['http://127.0.0.1:18793/', false],
  ['http://127.0.0.1/', false],
  ['ws://127.0.0.1:9999/ws', false],
  ['http://localhost:18792/', false],
  ['http://[::1]:18792/', false],
  ['file:///C:/Windows/win.ini', false],
  ['https://example.com/', false],
  ['https://media.valorant-api.com/maps/x.png', false],
  ['blob:https://evil.com/abc', false],
  ['ftp://127.0.0.1:18792/', false],
  ['javascript:alert(1)', false],
  ['not a url', false],
  ['', false],
];
for (const [url, want] of table) {
  test(`policy ${want ? 'ALLOW' : 'DENY '} ${url}`, () => assert.strictEqual(isAllowedUrl(url, P), want));
}
test('policy: non-string denied', () => assert.strictEqual(isAllowedUrl(null, P), false));
test('navigation: only exact app origin', () => {
  assert.ok(isAllowedNavigation('http://127.0.0.1:18792/analysis', P));
  assert.ok(!isAllowedNavigation('data:text/html,x', P));
  assert.ok(!isAllowedNavigation('https://example.com', P));
  assert.ok(!isAllowedNavigation('http://127.0.0.1.evil.com:18792/', P));
});
test('negative control: policy for a different port rejects the app URL', () => {
  assert.strictEqual(isAllowedUrl('http://127.0.0.1:18792/', 18793), false);
});
