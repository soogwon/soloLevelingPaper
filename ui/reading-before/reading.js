const states = { draft: '초안', completed: '작성 완료' };

function validSession(s, context) {
  if (!context || s?.context_id !== context || s.phase !== 'before' ||
      !Array.isArray(s.items) || s.total !== s.items.length) return false;
  const ids = new Set();
  let written = 0, completed = 0;
  for (const item of s.items) {
    if (!item) return false;
    const { template: t, answer: a } = item;
    if (!t || t.phase !== 'before' || typeof t.template_id !== 'string' || !t.template_id ||
        ids.has(t.template_id) || typeof t.prompt !== 'string') return false;
    ids.add(t.template_id);
    if (a === null) continue;
    if (!a || a.context_id !== context || a.phase !== 'before' || a.template_id !== t.template_id ||
        !Object.hasOwn(states, a.status) || typeof a.text !== 'string') return false;
    written++;
    if (a.status === 'completed') completed++;
  }
  return written === s.written && completed === s.completed;
}

export function mountReading(app, doc, timeoutMs = 15000) {
  const el = id => doc.getElementById(id);
  const notice = text => { el('notice').textContent = text; };
  const edits = new Map();
  let context = null, step, session = null, generation = 0;
  let connected = false, busy = false, needsRead = false;

  function render() {
    const disabled = !connected || busy || needsRead;
    el('refresh').disabled = !connected || !context || busy;
    el('counts').textContent = session
      ? `전체 ${session.total}개 · 작성 ${session.written}개 · 완료 ${session.completed}개` : '';
    el('items').replaceChildren();
    for (const {template, answer} of session?.items ?? []) {
      const id = template.template_id;
      const section = doc.createElement('section');
      const label = doc.createElement('label');
      const input = doc.createElement('textarea');
      input.id = 'reading-' + id;
      label.htmlFor = input.id;
      label.textContent = template.prompt;
      input.value = edits.get(id) ?? answer?.text ?? '';
      input.disabled = busy;
      const status = doc.createElement('small');
      function showStatus() {
        status.textContent = (answer ? states[answer.status] : '미작성') +
          (edits.has(id) ? ' · 저장 전 변경 있음' : '');
      }
      input.oninput = () => { edits.set(id, input.value); showStatus(); };
      showStatus();
      section.append(label, status, input);
      for (const [action, title] of [['draft', '초안 저장'], ['completed', '작성 완료']]) {
        const button = doc.createElement('button');
        button.type = 'button';
        button.textContent = title;
        button.disabled = disabled;
        button.onclick = () => {
          const text = input.value;
          if (Array.from(text).length > 5000 || (action === 'completed' && !text.trim())) {
            notice('답변은 5000자까지 입력할 수 있고, 작성 완료에는 내용이 필요합니다.');
            return;
          }
          call({template_id: id, text, status: action});
        };
        section.append(button);
      }
      el('items').append(section);
    }
  }

  async function call(save) {
    if (!context || !connected || busy || (save && needsRead)) return;
    const ticket = ++generation;
    busy = true;
    render();
    notice(save ? '답변을 저장하고 있습니다.' : '저장 상태를 확인하고 있습니다.');
    let timer;
    try {
      const result = await Promise.race([
        app.callServerTool({name: save ? 'save_reading_answer' : 'get_reading_questions',
          arguments: {context_id: context, phase: 'before', ...(save ?? (step ? {step} : {}))}}),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error()), timeoutMs); }),
      ]);
      if (ticket !== generation) return;
      const next = result.structuredContent;
      if (result.isError || !validSession(next, context)) throw new Error();
      if (save) {
        const answer = next.items.find(x => x.template.template_id === save.template_id)?.answer;
        if (answer?.text !== save.text || answer?.status !== save.status) throw new Error();
        edits.delete(save.template_id);
      } else {
        for (const {template, answer} of next.items) {
          if (answer && edits.get(template.template_id) === answer.text) edits.delete(template.template_id);
        }
      }
      session = next;
      needsRead = false;
      notice(save ? '답변을 저장했습니다.' : '저장 상태를 확인했습니다.');
    } catch {
      if (ticket !== generation) return;
      if (save) needsRead = true;
      notice(save ? '저장 결과를 확인할 수 없습니다. 다시 저장하기 전에 저장 상태를 조회하세요.'
                  : '조회할 수 없습니다. 다시 조회하거나 채팅의 도구 응답을 확인하세요.');
    } finally {
      clearTimeout(timer);
      if (ticket === generation) { busy = false; render(); }
    }
  }

  el('refresh').onclick = () => call();
  app.ontoolinput = ({arguments: args}) => {
    generation++;
    edits.clear();
    context = typeof args?.context_id === 'string' ? args.context_id : null;
    step = args?.step;
    session = null; busy = false; needsRead = false;
    notice('질문을 불러오는 중입니다.'); render();
  };
  app.ontoolresult = result => {
    if (busy || session) return;
    const next = result.structuredContent;
    if (!result.isError && validSession(next, context)) {
      session = next;
      notice(next.total ? '질문별로 답변을 저장할 수 있습니다.' : '이 단계에는 읽기 전 질문이 없습니다.');
    } else notice('응답을 확인할 수 없습니다. 저장 상태를 다시 조회하세요.');
    render();
  };
  app.ontoolcancelled = () => {
    generation++; context = null; session = null; busy = false; edits.clear();
    notice('요청이 취소되었습니다.'); render();
  };
  Promise.resolve().then(() => app.connect()).then(() => { connected = true; render(); })
    .catch(() => notice('화면 연결에 실패했습니다. 채팅의 도구 응답을 확인하세요.'));
}
