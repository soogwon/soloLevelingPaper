export function mountInsightImport(app, doc) {
  const el = id => doc.getElementById(id);
  let input, selection, selectionToken, submissionToken;
  let boxes = [], generation = 0, busy = false;

  function controls() {
    const count = boxes.filter(box => box.checked).length;
    el('choices').disabled = busy || !selection || Boolean(submissionToken);
    el('preview').disabled = el('choices').disabled || count < 1 || count > 20;
    el('submit').disabled = busy || !submissionToken || !el('agree').checked;
    el('cancel').disabled = busy || !submissionToken;
  }

  function clearReview() {
    submissionToken = null;
    el('agree').checked = false;
    el('review-text').textContent = '';
    el('review').hidden = true;
  }

  function reset() {
    ++generation;
    selection = selectionToken = null;
    busy = false;
    boxes = [];
    el('items').replaceChildren();
    el('destination').textContent = '';
    el('status').textContent = '';
    clearReview();
    controls();
  }

  const describe = item => `${item.kind === 'keyword' ? '키워드' : '중요 문장'} · ${item.text}\nPDF ${item.pdf_page ?? '?'}쪽 · 원문: ${item.quote_original}`;

  app.ontoolinput = event => { input = event.arguments; reset(); };
  app.ontoolinputpartial = app.ontoolcancelled = () => { input = null; reset(); };
  app.ontoolresult = result => {
    const data = result?._meta;
    const next = data?.selection;
    if (!input || !next || input.context_id !== next.local_context_id
        || input.remote_context_id?.toLowerCase() !== next.remote_context_id
        || (input.scope ?? 'paper') !== next.scope || !Array.isArray(next.items)
        || typeof data.selection_token !== 'string') {
      if (input && !next) el('status').textContent = '저장 항목을 불러오지 못했습니다.';
      return;
    }
    reset();
    selection = next;
    selectionToken = data.selection_token;
    el('destination').textContent = `대상 서버: ${selection.server} · 원격 학습 맥락: ${selection.remote_context_id}`;
    for (const item of selection.items) {
      const label = doc.createElement('label');
      const box = doc.createElement('input');
      box.type = 'checkbox';
      box.value = item.insight_id;
      box.onchange = controls;
      label.append(box, doc.createTextNode(describe(item)));
      el('items').append(label);
      boxes.push(box);
    }
    controls();
  };

  async function run(name, args, onResult) {
    if (busy) return;
    const current = generation;
    busy = true;
    controls();
    try {
      const reply = await app.callServerTool({name, arguments: args});
      if (current !== generation) return;
      if (reply?.isError) throw new Error();
      onResult(reply?._meta);
    } catch {
      if (current === generation) el('status').textContent = submissionToken
        ? '결과를 확인하지 못했습니다. 같은 확인으로 다시 시도할 수 있습니다.'
        : '미리보기를 확인하지 못했습니다.';
    } finally {
      if (current === generation) { busy = false; controls(); }
    }
  }

  el('preview').onclick = () => {
    if (el('preview').disabled) return;
    return run('preview_paper_insight_import', {
      context_id: selection.local_context_id, selection_token: selectionToken,
      insight_ids: boxes.filter(box => box.checked).map(box => box.value),
    }, data => {
      const review = data?.review;
      if (!review || typeof data.submission_token !== 'string' ||
          review.remote_context_id !== selection.remote_context_id || review.server !== selection.server) throw new Error();
      submissionToken = data.submission_token;
      el('review-text').textContent = `서버: ${review.server}\n원격 학습 맥락: ${review.remote_context_id}\nPDF 판본: ${review.server_version_id}\n게시본: ${review.publication_id}\n\n${review.items.map(describe).join('\n\n')}`;
      el('review').hidden = false;
      el('status').textContent = '선택한 원문 인용과 표시문은 아직 전송하지 않았습니다.';
    });
  };
  el('agree').onchange = controls;
  el('submit').onclick = () => {
    if (el('submit').disabled) return;
    return run('submit_paper_insight_import', {
      context_id: selection.local_context_id, submission_token: submissionToken,
    }, data => {
      if (data?.receipt?.status !== 'saved') {
        if (!data?.rejected) throw new Error();
        clearReview();
        el('status').textContent = '제출이 거부되었습니다. 대상 판본과 기존 이관 상태를 확인하세요.';
        return;
      }
      clearReview();
      selection = null;
      el('status').textContent = `원격 판본에 저장했습니다. 리비전 ${data.receipt.revision}`;
    });
  };
  el('cancel').onclick = () => {
    if (el('cancel').disabled) return;
    return run('cancel_paper_insight_import', {submission_token: submissionToken}, () => {
      clearReview();
      el('status').textContent = '제출 확인을 폐기했습니다.';
    });
  };
  reset();
  app.connect().catch(() => { reset(); el('status').textContent = '카드를 연결하지 못했습니다.'; });
}
