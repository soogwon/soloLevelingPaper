import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { mountRemotePdfRegistration } from './remote-pdf-registration.js';

const html = await readFile(new URL('./remote-pdf-registration-template.html', import.meta.url), 'utf8');
const url = 'https://example.invalid/#/uploads';

test('사용자가 누른 뒤에만 기존 웹 화면을 열고 실패하면 주소를 표시한다', async () => {
  const doc = new JSDOM(html).window.document;
  const calls = [];
  const app = {connect: async () => {}, openLink: async args => { calls.push(args); throw new Error(); }};
  mountRemotePdfRegistration(app, doc);
  const button = doc.getElementById('open');
  assert.equal(button.disabled, true);
  app.ontoolresult({structuredContent: {upload_url: url}});
  assert.deepEqual(calls, []);
  await button.onclick();
  assert.deepEqual(calls, [{url}]);
  assert.ok(doc.getElementById('fallback').textContent.includes(url));
  app.ontoolcancelled();
  assert.equal(button.disabled, true);
  app.ontoolresult({structuredContent: {upload_url: 'https://example.invalid/?token=abc#/uploads'}});
  assert.equal(button.disabled, true);
});
