// 화면에 표시한 리비전으로만 변경하며 결과가 불확실하면 재조회부터 한다.
export function mountRecordEditor(app, doc, item, initial, onChange) {
  let record = initial;
  const controls = doc.createElement('div');
  const status = doc.createElement('p');
  status.setAttribute('role', 'status');
  item.append(controls, status);

  function button(label, action) {
    const button = doc.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.onclick = action;
    controls.append(button);
  }

  function idle() {
    controls.replaceChildren();
    button('편집', edit);
  }

  function edit() {
    controls.replaceChildren();
    const input = doc.createElement('textarea');
    input.setAttribute('aria-label', record.kind === 'note' ? '메모 내용' : '하이라이트 설명');
    input.value = record.body;
    controls.append(input);
    button('수정 저장', () => {
      if (record.kind === 'note' && !input.value.trim()) {
        status.textContent = '메모 내용을 입력하세요.';
        return;
      }
      return run('update_annotation', input.value);
    });
    button('삭제', () => {
      controls.replaceChildren();
      status.textContent = '이 기록을 삭제하시겠습니까?';
      button('삭제 확인', () => run('delete_annotation'));
      button('취소', cancel);
    });
    button('취소', cancel);
    input.focus();
  }

  function cancel() {
    status.textContent = '';
    idle();
  }

  async function run(name, body) {
    if (!item.isConnected || !controls.childElementCount) return;
    controls.replaceChildren();
    const read = name === 'get_annotation';
    const request = {context_id: record.context_id, annotation_id: record.annotation_id};
    if (!read) request.expected_revision = record.revision;
    if (body !== undefined) request.body = body;
    status.textContent = '처리 중입니다.';
    try {
      const result = await app.callServerTool({name, arguments: {request}});
      if (!item.isConnected) return;
      if (result?.isError) throw new Error(result.content?.find(c => c.type === 'text')?.text);
      const data = result?.structuredContent;
      if (name === 'delete_annotation' && data?.outcome === 'deleted' && data.annotation === null) {
        onChange(null);
        return;
      }
      const next = data?.annotation;
      if (next?.context_id !== record.context_id || next.annotation_id !== record.annotation_id ||
          next.kind !== record.kind || typeof next.body !== 'string' || !Array.isArray(next.segments) ||
          !Number.isSafeInteger(next.revision) || next.revision < record.revision ||
          (!read && (name !== 'update_annotation' || data.outcome !== 'saved' ||
            next.revision !== record.revision + 1 || next.body !== body))) throw new Error();
      record = next;
      onChange(next);
      status.textContent = read ? '최신 기록을 불러왔습니다.' : '수정했습니다.';
      idle();
    } catch (error) {
      if (!item.isConnected) return;
      if (read && /\bNOT_FOUND:/.test(error.message)) {
        onChange(null);
        return;
      }
      status.textContent = /\bCONFLICT:/.test(error.message) ? '다른 변경이 먼저 저장됐습니다. 최신 기록을 다시 조회하세요.'
        : '결과를 확인하지 못했습니다. 최신 기록을 다시 조회하세요.';
      button('최신 기록 다시 조회', () => run('get_annotation'));
    }
  }
  idle();
}
