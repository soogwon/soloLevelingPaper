import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { mountPdfUpload } from './pdf-upload.js';

function setup() {
  const dom = new JSDOM(`<p id="file"></p><p id="notice"></p><input id="agree" type="checkbox">
    <button id="confirm"></button><button id="cancel"></button><p id="status"></p>`);
  const calls = [];
  const app = {connect: async () => {}, callServerTool: async input => {
    calls.push(input); return {structuredContent: {status: 'ready', upload_id: 'upload-1'}};
  }};
  mountPdfUpload(app, dom.window.document);
  app.ontoolinput({arguments: {paper_id: 'paper-1'}});
  return {app, doc: dom.window.document, calls};
}

test('승인 전에는 PDF를 전송하지 않고 카드 메타데이터를 표시한다', () => {
  const {app, doc, calls} = setup();
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'paper.pdf', file_size: 1234, notice: 'PDF 전체 전송',
  }, _meta: {confirmation_token: 'secret'}});
  assert.equal(doc.getElementById('file').textContent, 'paper.pdf · 1,234바이트');
  assert.equal(doc.getElementById('confirm').disabled, true);
  assert.equal(calls.length, 0);
});

test('체크 후 버튼만 숨겨진 업로드 도구를 호출한다', async () => {
  const {app, doc, calls} = setup();
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'paper.pdf', file_size: 1234, notice: 'PDF 전체 전송',
  }, _meta: {confirmation_token: 'secret'}});
  doc.getElementById('agree').checked = true;
  doc.getElementById('agree').onchange();
  await doc.getElementById('confirm').onclick();
  assert.deepEqual(calls, [{name: 'upload_paper_pdf_to_server', arguments: {
    paper_id: 'paper-1', version_id: 'version-1', confirmation_token: 'secret',
  }}]);
  assert.match(doc.getElementById('status').textContent, /완료/);
});
