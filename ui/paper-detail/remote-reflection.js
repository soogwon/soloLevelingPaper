export function mountRemoteReflection(app, doc, crypto = doc.defaultView.crypto) {
  const el = id => doc.getElementById(id);
  let context, scope, pending, epoch = 0, busy = false;

  function clear(message) {
    ++epoch;
    scope = pending = null;
    busy = false;
    el('answers').replaceChildren();
    el('comparison').textContent = '';
    el('consent').hidden = true;
    el('refresh').disabled = true;
    el('status').textContent = message;
  }
  function text(tag, value) {
    const node = doc.createElement(tag);
    node.textContent = value;
    return node;
  }
  function render(next, drafts = new Map()) {
    if (!next || next.context_id !== context || !Array.isArray(next.questions) ||
        !Array.isArray(next.answers) || !Array.isArray(next.groups) ||
        !/^[0-9a-f]{64}$/.test(next.card_binding) || typeof next.question_revision !== 'string') throw Error();
    scope = next;
    const saved = new Map(scope.answers.map(answer => [answer.template_id, answer]));
    const questions = new Map(scope.questions.map(question => [question.template_id, question]));
    const describe = id => {
      const answer = saved.get(id);
      return `${questions.get(id)?.prompt ?? ''}\n${answer?.status !== 'deleted' && answer?.body ? answer.body : '저장된 답변 없음'}`;
    };
    el('comparison').textContent = scope.groups.map(group =>
      [`읽기 전 · ${describe(group.before_id)}`, ...group.after_ids.map(id => `읽기 후 · ${describe(id)}`)].join('\n')
    ).join('\n\n');
    el('answers').replaceChildren();
    el('refresh').disabled = false;
    const allowed = scope.consent?.storage === 'current' &&
      scope.consent.question_revision === scope.question_revision && Boolean(scope.consent.consent_id);
    el('consent').hidden = allowed || !scope.consent_url;
    el('status').textContent = allowed ? '저장할 답변을 작성하고 확인해 주세요.' : '웹에서 성찰 답변 저장에 동의해 주세요.';
    for (const phase of ['before', 'after']) {
      const section = doc.createElement('section');
      section.append(text('h2', phase === 'before' ? '읽기 전' : '읽기 후'));
      for (const question of scope.questions.filter(q => q.phase === phase)) {
        const old = saved.get(question.template_id);
        const label = text('label', question.prompt);
        const box = doc.createElement('textarea');
        box.maxLength = 5000;
        box.dataset.templateId = question.template_id;
        box.disabled = !allowed;
        box.value = drafts.get(question.template_id) ?? (old?.status === 'deleted' ? '' : (old?.body ?? ''));
        label.append(box);
        const save = text('button', '답변 저장');
        save.disabled = !allowed;
        save.onclick = () => {
          if (busy || (pending && pending.button !== save) || !box.value.trim()) return;
          pending ??= {button: save, args: {
            context_id: context, expected_binding: scope.card_binding, request_key: crypto.randomUUID(),
            question_revision: scope.question_revision, template_id: question.template_id, body: box.value,
            status: 'completed', expected_revision: old?.revision ?? 0, storage_consent_id: scope.consent.consent_id,
          }};
          box.disabled = true;
          return run('save_remote_reflection_card', pending.args, result => {
            if (result.context_id !== context || result.template_id !== pending.args.template_id ||
                result.request_key !== pending.args.request_key || result.question_revision !== scope.question_revision) throw Error();
            const updated = [...scope.answers.filter(a => a.template_id !== result.template_id), result];
            pending = null;
            const drafts = new Map([...el('answers').querySelectorAll('textarea')]
              .filter(field => field.dataset.templateId !== result.template_id)
              .map(field => [field.dataset.templateId, field.value]));
            render({...scope, answers: updated}, drafts);
            el('status').textContent = '답변을 저장했습니다.';
          });
        };
        section.append(label, save);
      }
      el('answers').append(section);
    }
  }

  async function run(name, args, accept) {
    if (busy) return;
    const current = epoch;
    busy = true;
    try {
      const reply = await app.callServerTool({name, arguments: args});
      if (current !== epoch) return;
      const output = reply?.structuredContent;
      if (reply?.isError || output?.status !== 'ok') {
        if (['conflict', 'invalid_argument', 'not_authorized', 'not_found'].includes(output?.status)) {
          pending = null;
          el('answers').replaceChildren();
          el('comparison').textContent = '';
          el('status').textContent = '답변을 저장할 수 없습니다. 최신 저장 답변을 조회하거나 카드를 다시 열어 주세요.';
          return;
        }
        throw Error();
      }
      accept(output.result);
    } catch {
      if (current === epoch) el('status').textContent = pending
        ? '저장 결과를 확인하지 못했습니다. 같은 답변 저장 버튼으로 동일 요청을 다시 확인할 수 있습니다.'
        : '최신 답변을 불러오지 못했습니다.';
    } finally {
      if (current === epoch) busy = false;
    }
  }

  el('refresh').onclick = () => scope && run('show_remote_reading_reflection', {
    context_id: context, expected_binding: scope.card_binding,
  }, next => { pending = null; render(next); });
  el('consent').onclick = async () => {
    const current = epoch;
    try {
      const url = new URL(scope.consent_url);
      if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' ||
          url.search || url.hash !== `#/reflections/${context}/consent`) throw Error();
      const response = await app.openLink({url: url.href});
      if (response?.isError) throw Error();
    } catch {
      if (current === epoch) el('status').textContent = '동의 화면을 열지 못했습니다. 웹에서 성찰 저장 동의를 확인해 주세요.';
    }
  };
  app.ontoolinput = event => { clear('저장 답변을 읽는 중입니다.'); context = event.arguments?.context_id; };
  app.ontoolinputpartial = app.ontoolcancelled = () => { context = null; clear('카드를 다시 열어 주세요.'); };
  app.ontoolresult = result => {
    try {
      if (!context || result?.isError || result?.structuredContent?.status !== 'ok') throw Error();
      render(result.structuredContent.result);
    } catch { clear('성찰 카드를 열지 못했습니다. 맥락과 권한을 확인해 주세요.'); }
  };
  doc.defaultView?.addEventListener('pagehide', app.ontoolcancelled);
  app.connect().catch(() => clear('카드를 연결하지 못했습니다.'));
}
