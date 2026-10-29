const steps = { preview: '훑어보기', structure: '구조 파악', deep_read: '깊이 읽기', review: '복습' };
const statuses = { queued: '처리 대기', processing: '처리 중', ready: '검색 준비 완료', failed: '처리 실패', interrupted: '처리 중단' };
const stages = { download: '파일 준비', parse: '원문 추출', translate: '번역', index: '검색 색인' };
const goals = { understand: '논문 이해', implement: '구현 준비', skim: '빠르게 훑어보기' };
const kinds = { claim: '핵심 주장', method: '방법', result: '결과', limitation: '한계' };
const text = (v) => typeof v === 'string' && v.trim().length > 0;
const strings = (v) => Array.isArray(v) && v.every(text);
const ratio = (v) => Number.isFinite(v) && v >= 0 && v <= 1;

function valid(data) {
  if (!data || !text(data.context_id) || !text(data.version_id) || !text(data.paper_id)
      || typeof data.file_display_name !== 'string' || (data.title !== null && typeof data.title !== 'string')
      || !Object.hasOwn(goals, data.goal)) return false;
  const p = data.progress, job = data.processing;
  if (!p || !job || !text(job.job_id) || !Object.hasOwn(statuses, job.status)
      || (job.stage !== null && typeof job.stage !== 'string') || !strings(job.limitations)
      || typeof p.recorded !== 'boolean' || !Object.hasOwn(steps, p.current_step)
      || !strings(p.completed_steps) || !p.completed_steps.every((s) => Object.hasOwn(steps, s))
      || new Set(p.completed_steps).size !== p.completed_steps.length || p.total_steps !== 4
      || p.completion_ratio !== p.completed_steps.length / p.total_steps
      || (p.last_studied_at !== null && !text(p.last_studied_at))) return false;
  if (data.summary !== null && (!data.summary || !text(data.summary.summary_id)
      || !text(data.summary.created_at) || !Array.isArray(data.summary.items) || !data.summary.items.length
      || !data.summary.items.every((i) => i && Object.hasOwn(kinds, i.kind) && text(i.text)
        && strings(i.evidence_ids) && i.evidence_ids.length))) return false;
  return Array.isArray(data.quiz_scores) && data.quiz_scores.length === 4
    && data.quiz_scores.every((s, i) => s && s.step === Object.keys(steps)[i]
      && Number.isInteger(s.attempt_count) && s.attempt_count >= 0
      && (s.attempt_count === 0
        ? [s.latest_attempt_id, s.latest_correct, s.latest_total, s.latest_score, s.best_score].every((v) => v === null)
        : text(s.latest_attempt_id) && Number.isInteger(s.latest_correct) && s.latest_correct >= 0
          && Number.isInteger(s.latest_total) && s.latest_total > 0 && s.latest_correct <= s.latest_total
          && ratio(s.latest_score) && s.latest_score === s.latest_correct / s.latest_total
          && ratio(s.best_score) && s.best_score >= s.latest_score));
}

export function mountDashboard(app, doc, timeoutMs = 15000) {
  const el = (id) => doc.getElementById(id);
  let contextId = null, connected = false, busy = true, revision = 0, waiting;
  function message(value, loading = false) {
    busy = loading;
    el('message').textContent = value;
    doc.querySelector('main').setAttribute('aria-busy', String(loading));
    el('refresh').disabled = !connected || !contextId || busy;
  }
  function hide() { el('details').hidden = true; }
  function wait() {
    clearTimeout(waiting);
    const current = revision;
    waiting = setTimeout(() => {
      if (current === revision) message('호스트 연결 또는 결과가 지연되고 있습니다. 채팅의 응답을 확인하세요.');
    }, timeoutMs);
  }
  function display(result) {
    clearTimeout(waiting);
    hide();
    if (result?.isError) { message('현황 조회에 실패했습니다. 채팅의 안내를 확인하세요.'); return; }
    const data = result?.structuredContent;
    if (!valid(data) || (contextId && data.context_id !== contextId)) {
      message('응답 형식 또는 학습 맥락을 확인하지 못했습니다. 채팅에서 다시 조회해 주세요.'); return;
    }
    contextId = data.context_id;
    const p = data.progress;
    el('paper-title').textContent = data.title || data.file_display_name || '제목 없는 논문';
    el('identity').textContent = `${data.context_id} · ${data.version_id}`;
    el('goal').textContent = goals[data.goal];
    el('processing').textContent = `${statuses[data.processing.status]} · ${stages[data.processing.stage] ?? data.processing.stage ?? '단계 정보 없음'}`;
    el('job').textContent = `작업 ID: ${data.processing.job_id}`;
    el('limitations').replaceChildren();
    for (const value of data.processing.limitations) {
      const li = doc.createElement('li'); li.textContent = value; el('limitations').append(li);
    }
    el('progress-label').textContent = p.recorded
      ? `${p.completed_steps.length} / ${p.total_steps}단계 완료 · ${Math.round(p.completion_ratio * 100)}%`
      : '진행 기록 없음 · 첫 단계부터 시작할 수 있습니다.';
    el('progress').value = p.completion_ratio;
    el('steps').replaceChildren();
    for (const [key, label] of Object.entries(steps)) {
      const li = doc.createElement('li');
      li.textContent = `${label} · ${p.completed_steps.includes(key) ? '완료' : p.recorded && p.current_step === key ? '현재 단계' : '미완료'}`;
      el('steps').append(li);
    }
    el('last-studied').textContent = p.last_studied_at ? `마지막 학습: ${p.last_studied_at}` : '마지막 학습 기록 없음';
    el('summary-empty').hidden = data.summary !== null;
    el('summary-list').replaceChildren();
    for (const item of data.summary?.items ?? []) {
      const li = doc.createElement('li');
      li.textContent = `${kinds[item.kind]} — ${item.text}`;
      const evidence = doc.createElement('span'); evidence.className = 'evidence';
      evidence.textContent = `근거 ID: ${item.evidence_ids.join(', ')}`;
      li.append(evidence); el('summary-list').append(li);
    }
    el('quiz').replaceChildren();
    for (const score of data.quiz_scores) {
      const tr = doc.createElement('tr');
      const values = [steps[score.step], `${score.attempt_count}회`,
        score.attempt_count ? `${score.latest_correct} / ${score.latest_total}문항 (${Math.round(score.latest_score * 100)}%)` : '퀴즈 기록 없음',
        score.best_score === null ? '—' : `${Math.round(score.best_score * 100)}%`];
      for (const value of values) { const td = doc.createElement('td'); td.textContent = value; tr.append(td); }
      el('quiz').append(tr);
    }
    message('저장된 학습 현황입니다.');
    el('details').hidden = false;
  }
  // 새 입력과 취소 뒤에 도착한 이전 새로고침 응답은 화면을 덮어쓰지 않는다.
  app.ontoolinput = ({ arguments: args }) => {
    revision += 1; contextId = text(args?.context_id) ? args.context_id : null;
    hide(); message('학습 현황을 불러오고 있습니다.', true); wait();
  };
  app.ontoolresult = (result) => { revision += 1; display(result); };
  app.ontoolcancelled = () => { revision += 1; clearTimeout(waiting); hide(); message('조회가 취소되었습니다. 채팅에서 다시 조회할 수 있습니다.'); };
  el('refresh').addEventListener('click', async () => {
    if (!connected || !contextId || busy) return;
    const current = ++revision; let timer;
    hide(); message('학습 현황을 새로 확인하고 있습니다.', true);
    try {
      const result = await Promise.race([
        app.callServerTool({ name: 'get_learning_dashboard', arguments: { context_id: contextId } }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), timeoutMs); }),
      ]);
      if (current === revision) display(result);
    } catch { if (current === revision) message('조회가 지연되거나 실패했습니다. 다시 조회하거나 채팅의 응답을 확인하세요.'); }
    finally { clearTimeout(timer); }
  });
  wait();
  Promise.resolve().then(() => app.connect()).then(() => {
    connected = true; el('refresh').disabled = !contextId || busy;
  }).catch(() => { clearTimeout(waiting); hide(); message('화면을 연결하지 못했습니다. 채팅의 도구 응답으로 확인하세요.'); });
}
