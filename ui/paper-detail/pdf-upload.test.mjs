import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { mountPdfUpload } from './pdf-upload.js';

function setup() {
  const dom = new JSDOM(`<p id="file"></p><p id="notice"></p><input id="agree" type="checkbox">
    <button id="confirm"></button><button id="cancel"></button><button id="retry" hidden></button>
    <p id="status"></p>`);
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
    confirmation_token: 'secret',
  }});
  assert.equal(doc.getElementById('file').textContent, 'paper.pdf · 1,234바이트');
  assert.equal(doc.getElementById('confirm').disabled, true);
  assert.equal(calls.length, 0);
});

test('호스트가 결과 메타를 제거해도 구조화 응답 토큰으로 승인할 수 있다', () => {
  const {app, doc} = setup();
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'paper.pdf', file_size: 1234, notice: 'PDF 전체 전송',
    confirmation_token: 'secret',
  }});
  assert.equal(doc.getElementById('agree').disabled, false);
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

test('같은 논문의 다른 판본 응답은 승인 대상에 넣지 않는다', () => {
  const {app, doc} = setup();
  app.ontoolinput({arguments: {paper_id: 'paper-1', version_id: 'version-2'}});
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'old.pdf', file_size: 1234, notice: 'PDF 전체 전송',
  }, _meta: {confirmation_token: 'old'}});
  assert.equal(doc.getElementById('agree').disabled, true);
  assert.equal(doc.getElementById('file').textContent, '');
});

test('파일 전송 실패 이유를 표시하고 자동 재전송하지 않는다', async () => {
  const {app, doc, calls} = setup();
  app.callServerTool = async input => {
    calls.push(input);
    return {isError: true, content: [{type: 'text',
      text: 'PDF_UPLOAD_TRANSFER_FAILED: PDF 파일 전송에 실패했습니다. (HTTP 403)'}]};
  };
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'paper.pdf', file_size: 1234, notice: 'PDF 전체 전송',
    confirmation_token: 'secret',
  }});
  doc.getElementById('agree').checked = true;
  doc.getElementById('agree').onchange();
  await doc.getElementById('confirm').onclick();
  assert.match(doc.getElementById('status').textContent, /저장소로 전송하지 못했습니다/);
  assert.match(doc.getElementById('status').textContent, /자동으로 다시 전송하지 않습니다/);
  assert.equal(doc.getElementById('retry').hidden, false);
  assert.equal(calls.length, 1);
});

test('재시도는 새 안내만 요청하고 다시 동의를 요구한다', async () => {
  const {app, doc, calls} = setup();
  app.callServerTool = async input => {
    calls.push(input);
    if (input.name === 'upload_paper_pdf_to_server') throw new Error('network');
    return {structuredContent: {
      status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-2',
      file_name: 'paper.pdf', file_size: 1234, notice: '새 안내',
      confirmation_token: 'new-secret',
    }};
  };
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-2',
    file_name: 'paper.pdf', file_size: 1234, notice: '기존 안내',
    confirmation_token: 'old-secret',
  }});
  doc.getElementById('agree').checked = true;
  doc.getElementById('agree').onchange();
  await doc.getElementById('confirm').onclick();
  await doc.getElementById('retry').onclick();
  assert.equal(calls[1].name, 'prepare_paper_pdf_upload');
  assert.deepEqual(calls[1].arguments, {paper_id: 'paper-1'});
  assert.equal(doc.getElementById('notice').textContent, '새 안내');
  assert.equal(doc.getElementById('agree').checked, false);
  assert.equal(doc.getElementById('confirm').disabled, true);
});

test('이미 업로드된 원문은 성공 상태로 안내하고 재시도를 숨긴다', async () => {
  const {app, doc} = setup();
  app.callServerTool = async () => ({isError: true, content: [{type: 'text',
    text: 'PDF_ALREADY_UPLOADED: 이 원문 버전의 PDF가 이미 준비되어 있습니다.'}]});
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'paper.pdf', file_size: 1234, notice: 'PDF 전체 전송',
    confirmation_token: 'secret',
  }});
  doc.getElementById('agree').checked = true;
  doc.getElementById('agree').onchange();
  await doc.getElementById('confirm').onclick();
  assert.match(doc.getElementById('status').textContent, /이미 업로드된 원문/);
  assert.equal(doc.getElementById('retry').hidden, true);
});

test('호스트 오류 채널 없이 이미 업로드 상태를 안내한다', async () => {
  const {app, doc} = setup();
  app.callServerTool = async () => ({structuredContent: {
    status: 'already_uploaded', error_code: 'PDF_ALREADY_UPLOADED',
  }});
  app.ontoolresult({structuredContent: {
    status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
    file_name: 'paper.pdf', file_size: 1234, notice: 'PDF 전체 전송',
    confirmation_token: 'secret',
  }});
  doc.getElementById('agree').checked = true;
  doc.getElementById('agree').onchange();
  await doc.getElementById('confirm').onclick();
  assert.match(doc.getElementById('status').textContent, /이미 업로드된 원문/);
  assert.equal(doc.getElementById('retry').hidden, true);
});

for (const [code, message] of [
  ['PDF_UPLOAD_PREPARE_FAILED', /준비를 만들지 못했습니다/],
  ['PDF_UPLOAD_TRANSFER_FAILED', /저장소로 전송하지 못했습니다/],
  ['PDF_UPLOAD_COMPLETE_FAILED', /완료 여부를 확인하지 못했습니다/],
  ['PRIVATE_UNKNOWN', /결과를 확인하지 못했습니다/],
]) {
  test(`구조화 실패 ${code}는 자동 재전송 없이 새 동의를 요구한다`, async () => {
    const {app, doc, calls} = setup();
    app.callServerTool = async call => {
      calls.push(call);
      return {structuredContent: {status: 'failed', error_code: code}};
    };
    app.ontoolresult({structuredContent: {
      status: 'confirmation_required', paper_id: 'paper-1', version_id: 'version-1',
      file_name: 'paper.pdf', file_size: 1234, notice: '안내', confirmation_token: 'secret',
    }});
    doc.getElementById('agree').checked = true;
    await doc.getElementById('confirm').onclick();
    assert.match(doc.getElementById('status').textContent, message);
    assert.doesNotMatch(doc.getElementById('status').textContent, /PRIVATE_UNKNOWN/);
    assert.equal(doc.getElementById('retry').hidden, false);
    assert.equal(doc.getElementById('confirm').disabled, true);
    await doc.getElementById('confirm').onclick();
    assert.equal(calls.length, 1);
  });
}

for (const [name, response] of [
  ['도구 오류', {isError: true, content: [{type: 'text', text: 'private error'}]}],
  ['빈 응답', {}],
  ['다른 논문', {structuredContent: {paper_id: 'other'}}],
  ['잘못된 안내', {structuredContent: {paper_id: 'paper-1'}}],
]) {
  test(`재시도 안내의 ${name} 뒤에도 다시 요청할 수 있다`, async () => {
    const {app, doc, calls} = setup();
    const preview = {status: 'confirmation_required', paper_id: 'paper-1',
      version_id: 'version-1', file_name: 'paper.pdf', file_size: 1234,
      notice: '안내', confirmation_token: 'secret'};
    app.ontoolresult({structuredContent: preview});
    app.callServerTool = async call => {
      calls.push(call);
      return calls.length <= 2 ? response : {structuredContent: preview};
    };
    doc.getElementById('agree').checked = true;
    await doc.getElementById('confirm').onclick();
    await doc.getElementById('retry').onclick();
    assert.equal(doc.getElementById('retry').hidden, false);
    assert.equal(doc.getElementById('agree').disabled, true);
    assert.doesNotMatch(doc.getElementById('status').textContent, /private/);
    await doc.getElementById('retry').onclick();
    assert.equal(calls.length, 3);
    assert.equal(calls[2].name, 'prepare_paper_pdf_upload');
    assert.equal(doc.getElementById('agree').disabled, false);
    assert.equal(doc.getElementById('agree').checked, false);
    assert.equal(doc.getElementById('confirm').disabled, true);
  });
}
