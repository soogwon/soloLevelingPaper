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
    controls();
  }
  app.ontoolinputpartial = app.ontoolcancelled = () => { input = null; reset(); };
  app.ontoolinput = event => { input = event.arguments; reset(); };
  app.ontoolresult = result => {
    const data = result?.structuredContent;
    if (!input || input.paper_id !== data?.paper_id
        || (input.version_id != null && input.version_id !== data?.version_id)) return;
    reset();
    if (!result?.isError && data?.status === 'confirmation_required'
        && typeof data.notice === 'string' && typeof data.paper_id === 'string'
        && typeof data.version_id === 'string' && typeof data.file_name === 'string'
        && Number.isSafeInteger(data.file_size) && data.file_size > 0
        && typeof result._meta?.confirmation_token === 'string') {
      pending = {...data, confirmation_token: result._meta.confirmation_token};
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
      if (result?.isError || !output || (accept ? output.status !== 'ready'
          : output.status !== 'cancelled')) throw new Error();
      el('status').textContent = accept
        ? `PDF 업로드를 완료했습니다. 업로드 ID: ${output.upload_id}`
        : '취소했습니다. PDF를 전송하지 않았습니다.';
    } catch {
      if (current !== generation) return;
      el('status').textContent = accept
        ? 'PDF 업로드 결과를 확인하지 못했습니다. 자동으로 다시 전송하지 않습니다.'
        : '취소 결과를 확인하지 못했습니다.';
    }
    if (current === generation) { pending = null; busy = false; controls(); }
  }
  el('confirm').onclick = () => submit(true);
  el('cancel').onclick = () => submit(false);
  controls();
  app.connect().catch(() => {
    ++generation; pending = null; busy = false; controls();
    el('status').textContent = '카드 연결에 실패했습니다. PDF를 전송하지 않았습니다.';
  });
}
