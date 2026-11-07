export function mountQuestionCard(app, doc) {
  const el = id => doc.getElementById(id);
  const list = el('requests'), input = el('question'), output = el('result');
  let context, version = 0, busy = false, ready = false;
  // 입력 없이 재생된 최초 결과도 복원한다. null은 유효한 새 입력을 기다리는 상태다.
  let expectedContext;
  function controls() {
    el('submit').disabled = busy || !ready || !input.value.trim();
    list.disabled = el('restore').disabled = busy || !list.value;
    input.disabled = busy || !ready;
  }
  async function tool(name, args) {
    const reply = await app.callServerTool({name, arguments: args});
    if (reply?.isError || !reply?.structuredContent) throw new Error();
    return reply.structuredContent;
  }
  function display(data) {
    const states = {running: '실행 중입니다.', in_progress: '실행 중입니다.', failed: '질문 실행에 실패했습니다.',
      expired: '저장 기간이 지났습니다.', not_found: '서버에서 해당 요청을 찾을 수 없습니다.'};
    output.textContent = data.state === 'completed' && data.answer
      ? [data.answer.status, data.answer.answer_ko, '인용 ' + data.answer.citations.length + '건'].join('\n')
      : states[data.state] || '실행 상태를 확인할 수 없습니다.';
  }
  async function lookup(current) {
    const data = await tool('get_paper_question_status', {context_id: context, request_key: list.value});
    if (current === version) {
      display(data);
      ready = true;
    }
  }
  async function action(fn) {
    const current = version;
    busy = true; controls();
    try { await fn(current); }
    catch { if (current === version) output.textContent = list.value
      ? '요청을 확인하지 못했습니다. 결과 조회를 눌러 다시 확인하세요.'
      : '요청을 확인하지 못했습니다. 같은 질문 카드를 다시 열어 확인하세요.'; }
    finally { if (current === version) { busy = false; controls(); } }
  }
  function reset(message) {
    ++version;
    context = undefined;
    busy = ready = false;
    input.value = '';
    list.replaceChildren();
    output.textContent = message;
    controls();
  }
  app.ontoolinputpartial = () => {
    expectedContext = null;
    reset('학습 맥락을 기다리고 있습니다.');
  };
  app.ontoolinput = ({arguments: args}) => {
    expectedContext = args?.context_id ?? null;
    reset('학습 맥락을 기다리고 있습니다.');
  };
  app.ontoolcancelled = () => {
    expectedContext = null;
    reset('요청이 취소되었습니다.');
  };
  app.ontoolresult = async reply => {
    const received = reply?.structuredContent?.context_id;
    if (expectedContext === null || (received && expectedContext !== undefined && received !== expectedContext)) return;
    if (reply?.isError || typeof received !== 'string' || !received.trim()) {
      reset('학습 맥락을 확인할 수 없습니다.');
      return;
    }
    reset('저장된 요청을 확인하고 있습니다.');
    context = received;
    await action(async current => {
      const data = await tool('list_card_question_keys', {context_id: context});
      if (current !== version) return;
      for (const row of data.requests) {
        const option = doc.createElement('option');
        option.value = row.request_key;
        option.textContent = new Date(row.created_at * 1000).toLocaleString() + ' · ' + row.request_key.slice(0, 8);
        list.append(option);
      }
      if (list.value) await lookup(current);
      else output.textContent = '이 카드에서 제출한 질문이 없습니다.';
      if (current === version) ready = true;
    });
  };
  input.oninput = controls;
  list.onchange = () => { output.textContent = '선택한 요청의 결과를 조회하세요.'; };
  el('restore').onclick = () => action(lookup);
  el('submit').onclick = () => {
    if (el('submit').disabled) return;
    const question = input.value.trim();
    return action(async current => {
      const {request_key} = await tool('reserve_card_question_key', {context_id: context});
      if (current !== version) return;
      if (typeof request_key !== 'string' || !request_key) throw new Error();
      const option = doc.createElement('option');
      option.value = request_key; option.textContent = '방금 제출한 질문 · ' + request_key.slice(0, 8);
      list.prepend(option); list.value = request_key;
      input.value = ''; output.textContent = '답변을 기다리고 있습니다.';
      const data = await tool('submit_paper_question', {context_id: context, request_key, question});
      if (current === version) display(data);
    });
  };
  controls();
  app.connect().catch(() => reset('카드를 연결하지 못했습니다.'));
}
