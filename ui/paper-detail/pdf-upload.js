export function mountPdfUpload(app, doc) {
  const el = id => doc.getElementById(id);
  let pending = null, busy = false, input = null, generation = 0;
  function controls() {
    el('agree').disabled = busy || !pending;
    el('confirm').disabled = busy || !pending || !el('agree').checked;
    el('cancel').disabled = busy || !pending;
  }
  function reset() {
    ++generation; pending = null; busy = false; el('agree').checked = false;
    el('file').textContent = ''; el('notice').textContent = ''; el('status').textContent = '';
    el('retry').hidden = true;
    controls();
  }
  function errorText(value) {
    if (typeof value === 'string') return value;
    const content = value?.content;
    if (!Array.isArray(content)) return '';
    return content.map(item => item?.type === 'text' ? item.text : '').filter(Boolean).join('\n');
  }
  function uploadFailure(value) {
    const text = errorText(value);
    if (text.includes('PDF_ALREADY_UPLOADED'))
      return {alreadyUploaded: true, message: '이미 업로드된 원문입니다. 새로 전송하지 않았습니다.'};
    const messages = {
      PDF_UPLOAD_PREPARE_FAILED: '서버에서 업로드 준비를 만들지 못했습니다.',
      PDF_UPLOAD_TRANSFER_FAILED: 'PDF 파일을 저장소로 전송하지 못했습니다.',
      PDF_UPLOAD_COMPLETE_FAILED: '서버에서 업로드 완료 여부를 확인하지 못했습니다.',
      SERVER_CONNECTION_REQUIRED: '서버 계정 연결이 만료되었습니다. 계정을 다시 연결하세요.',
      PDF_UPLOAD_PREVIEW_CHANGED: '승인 후 PDF 파일이 변경되었습니다.',
    };
    const code = Object.keys(messages).find(key => text.includes(key));
    return {alreadyUploaded: false, message: code ? messages[code]
      : 'PDF 업로드 결과를 확인하지 못했습니다.'};
  }
  async function recoverUploadStatus(data) {
    try {
      const result = await app.callServerTool({
        name: 'check_paper_pdf_upload_status',
        arguments: {paper_id: data.paper_id, version_id: data.version_id,
          confirmation_token: data.confirmation_token},
      });
      const output = result?.structuredContent;
      if (!result?.isError && ['ready', 'failed', 'not_uploaded'].includes(output?.status))
        return output;
    } catch { /* 상태 확인 실패 시에도 원문을 다시 전송하지 않는다. */ }
    return null;
  }
  app.ontoolinputpartial = app.ontoolcancelled = () => { input = null; reset(); };
  app.ontoolinput = event => { input = event.arguments; reset(); };
  app.ontoolresult = result => {
    const data = result?.structuredContent;
    const confirmationToken = data?.confirmation_token ?? result?._meta?.confirmation_token;
    if (!input || input.paper_id !== data?.paper_id
        || (input.version_id != null && input.version_id !== data?.version_id)) return;
    reset();
    if (!result?.isError && data?.status === 'confirmation_required'
        && typeof data.notice === 'string' && typeof data.paper_id === 'string'
        && typeof data.version_id === 'string' && typeof data.file_name === 'string'
        && Number.isSafeInteger(data.file_size) && data.file_size > 0
        && typeof confirmationToken === 'string') {
      pending = {...data, confirmation_token: confirmationToken};
      el('file').textContent = `${data.file_name} · ${data.file_size.toLocaleString()}바이트`;
      el('notice').textContent = data.notice;
      el('status').textContent = '아직 PDF를 서버에 전송하지 않았습니다.';
    } else el('status').textContent = '안내를 불러오지 못했습니다. PDF 업로드 안내를 다시 요청하세요.';
    controls();
  };
  el('agree').onchange = controls;
  async function submit(accept) {
    if (!pending || busy || (accept && !el('agree').checked)) return;
    const data = pending, current = generation; busy = true; controls();
    el('status').textContent = accept ? 'PDF를 업로드하고 검증하는 중입니다.' : '취소 중입니다.';
    try {
      const result = await app.callServerTool({
        name: accept ? 'upload_paper_pdf_to_server' : 'cancel_paper_pdf_upload',
        arguments: accept ? {paper_id: data.paper_id, version_id: data.version_id,
          confirmation_token: data.confirmation_token}
          : {confirmation_token: data.confirmation_token},
      });
      if (current !== generation) return;
      const output = result?.structuredContent;
      if (result?.isError) throw Object.assign(new Error(errorText(result)), {toolResult: result});
      if (accept && output?.status === 'already_uploaded') {
        el('status').textContent = '이미 업로드된 원문입니다. 새로 전송하지 않았습니다.';
        el('retry').hidden = true;
      } else if (accept && output?.status === 'failed') {
        const recovered = output.error_code === 'PDF_UPLOAD_COMPLETE_FAILED'
          ? await recoverUploadStatus(data) : null;
        if (current !== generation) return;
        if (recovered?.status === 'ready') {
          el('status').textContent = 'PDF 업로드를 완료했습니다. 서버에서 저장 상태를 확인했습니다.';
          el('retry').hidden = true;
        } else {
          const failure = uploadFailure(output.error_code);
          el('status').textContent = `${failure.message} 자동으로 다시 전송하지 않습니다. 업로드 안내를 다시 불러온 뒤 재시도하세요.`;
          el('retry').hidden = false;
        }
      } else {
        if (!output || (accept ? output.status !== 'ready'
            : output.status !== 'cancelled')) throw new Error();
        el('status').textContent = accept
          ? `PDF 업로드를 완료했습니다. 업로드 ID: ${output.upload_id}`
          : '취소했습니다. PDF를 전송하지 않았습니다.';
      }
    } catch (error) {
      if (current !== generation) return;
      if (accept) {
        const originalFailure = uploadFailure(error?.toolResult ?? error?.message);
        const errorCode = errorText(error?.toolResult ?? error?.message);
        const shouldRecover = !originalFailure.alreadyUploaded
          && (!error?.toolResult || errorCode.includes('PDF_UPLOAD_COMPLETE_FAILED'));
        if (shouldRecover)
          el('status').textContent = '응답이 불확실하여 서버의 업로드 상태를 확인하는 중입니다.';
        const recovered = shouldRecover ? await recoverUploadStatus(data) : null;
        if (current !== generation) return;
        if (recovered?.status === 'ready') {
          el('status').textContent = 'PDF 업로드를 완료했습니다. 서버에서 저장 상태를 확인했습니다.';
          el('retry').hidden = true;
        } else {
          const failure = recovered?.status === 'failed'
            ? uploadFailure(recovered.reason) : originalFailure;
          el('status').textContent = failure.alreadyUploaded ? failure.message
            : `${failure.message} 자동으로 다시 전송하지 않습니다. 업로드 안내를 다시 불러온 뒤 재시도하세요.`;
          el('retry').hidden = failure.alreadyUploaded;
        }
      } else el('status').textContent = '취소 결과를 확인하지 못했습니다.';
    }
    if (current === generation) { pending = null; busy = false; controls(); }
  }
  el('retry').onclick = async () => {
    if (busy || !input?.paper_id) return;
    const current = ++generation; busy = true; pending = null; el('retry').hidden = true;
    el('agree').checked = false; controls();
    el('status').textContent = '업로드 안내를 다시 불러오는 중입니다.';
    try {
      const result = await app.callServerTool({name: 'prepare_paper_pdf_upload', arguments: {
        paper_id: input.paper_id, ...(input.version_id == null ? {} : {version_id: input.version_id}),
      }});
      if (current !== generation) return;
      app.ontoolresult(result);
      if (!pending) {
        busy = false; el('retry').hidden = false; controls();
        el('status').textContent = '업로드 안내를 다시 불러오지 못했습니다. 잠시 후 다시 시도하세요.';
      }
    } catch {
      if (current !== generation) return;
      busy = false; el('retry').hidden = false; controls();
      el('status').textContent = '업로드 안내를 다시 불러오지 못했습니다. 잠시 후 다시 시도하세요.';
    }
  };
  el('confirm').onclick = () => submit(true);
  el('cancel').onclick = () => submit(false);
  controls();
  app.connect().catch(() => {
    ++generation; pending = null; busy = false; controls();
    el('status').textContent = '카드 연결에 실패했습니다. PDF를 전송하지 않았습니다.';
  });
}
