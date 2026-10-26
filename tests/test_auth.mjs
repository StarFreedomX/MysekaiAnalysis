import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { credentials, sha256 } from '../static/auth.js';

// Check the portable implementation against Node's implementation, including block boundaries.
for (const text of ['', 'abc', '中文', 'a'.repeat(55), 'a'.repeat(56), 'a'.repeat(64), 'a'.repeat(1000), '123456789_482916']) {
  assert.equal(sha256(text), createHash('sha256').update(text).digest('hex'));
}
const auth = credentials('123456789', '482916');
assert.deepEqual(auth, { uid: '123456789', token: sha256('123456789_482916') });
assert.notEqual(auth.token, credentials('123456789', '482917').token);
assert.notEqual(auth.token, credentials('987654321', '482916').token);
assert.throws(() => credentials('123456789', '12345'));
assert.throws(() => credentials('123456789', 'abcdef'));
assert.throws(() => credentials('１２３４５６', '482916'));

// Shadowrocket transmits the already-derived token, never the password.
const binary = new Uint8Array([0, 255, 1, 128]);
let request;
let finished = false;
vm.runInNewContext(readFileSync(new URL('../static/upload.js', import.meta.url), 'utf8'), {
  $argument: `https://example.test|${auth.uid}|${auth.token}|cn`,
  $request: {url: `https://mkcn-prod-public-60001-1.dailygn.com/api/user/${auth.uid}/mysekai?isForceAllReloadOnlyMysekai=True`},
  $response: {body: binary},
  $notification: {post() {}},
  $done() {finished = true;},
  $httpClient: {post(options, callback) {request = options; callback(null, {status: 200}, '{"ok":true}');}}
});
assert.equal(request.url, 'https://example.test/api/upload');
assert.equal(request.headers.Authorization, `Bearer ${auth.token}`);
assert.equal(request.headers['X-User-Id'], auth.uid);
assert.strictEqual(request.body, binary);
assert(!JSON.stringify(request.headers).includes('482916'));
assert(finished);
console.log('PASS: SHA-256 vectors, credential derivation and Shadowrocket upload headers');
