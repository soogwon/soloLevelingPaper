const statuses = {
  queued: '처리를 기다리고 있습니다.', processing: '논문을 처리하고 있습니다.',
  ready: '검색 준비가 완료되었습니다.', failed: '처리에 실패했습니다.',
  interrupted: '이전 처리가 중단되었습니다.',
};
const stages = { download: '파일 준비', parse: '원문 추출', translate: '번역', index: '검색 색인' };

export function mountStatusApp(app, doc, timeoutMs = 15000) {
  const el = (id) => doc.getElementById(id);
  const button = el('refresh');
  let jobId = null;
  let connected = false;
  let busy = true;
  let revision = 0;
  let connectionTimer;

  function message(text, loading = false) {
    busy = loading;
    el('message').textContent = text;
    doc.querySelector('main').setAttribute('aria-busy', String(loading));
    button.disabled = !connected || !jobId || loading;
  }

  function startWaitTimer() {
    clearTimeout(connectionTimer);
    const waitingRevision = revision;
    connectionTimer = setTimeout(() => {
      // 이전 입력의 타이머가 새 결과나 새 조회 상태를 덮어쓰지 않게 한다.
      if (waitingRevision !== revision) return;
      message('호스트 연결 또는 결과가 지연되고 있습니다. 채팅의 도구 응답을 확인하세요.');
    }, timeoutMs);
  }

  function display(result) {
    clearTimeout(connectionTimer);
    el('details').hidden = true;
    if (result?.isError) {
      // 오류 전문에는 입력값이 포함될 수 있어 그대로 화면에 옮기지 않는다.
      message('상태 조회에 실패했습니다. 채팅에서 작업 ID와 오류 안내를 확인하세요.');
      return;
    }
    const data = result?.structuredContent;
    if (!data || Object.keys(data).length === 0) {
      message('표시할 상태가 없습니다. 채팅에서 작업 상태를 조회해 주세요.');
      return;
    }
    if (!Object.hasOwn(statuses, data.status) || typeof data.job_id !== 'string' || !data.job_id.trim()
        || typeof data.version_id !== 'string' || !Array.isArray(data.limitations)
        || !data.limitations.every((item) => typeof item === 'string')
        || !Array.isArray(data.capabilities) || typeof data.result_available !== 'boolean'
        || (data.stage !== null && typeof data.stage !== 'string')) {
      message('상태 응답 형식을 확인하지 못했습니다. 채팅의 응답을 확인하세요.');
      return;
    }
    jobId = data.job_id;
    message(statuses[data.status]);
    el('stage').textContent = stages[data.stage] ?? data.stage ?? '아직 시작하지 않음';
    el('job').textContent = data.job_id;
    el('version').textContent = data.version_id;
    el('limitations').replaceChildren();
    for (const limitation of data.limitations.length ? data.limitations : ['보고된 제한 사항이 없습니다.']) {
      const item = doc.createElement('li');
      item.textContent = limitation;
      el('limitations').append(item);
    }
    el('next').textContent = data.status === 'ready'
      ? '채팅에서 학습을 시작할 수 있습니다. 검색 준비 완료는 전체 번역 완료를 뜻하지 않습니다.'
      : '상태 새로고침은 조회만 수행합니다. 실패·중단된 작업을 자동 재시도하지 않습니다.';
    el('details').hidden = false;
  }

  // 호스트가 초기 결과를 즉시 보내도 놓치지 않도록 연결 전에 등록한다.
  app.ontoolinput = ({ arguments: args }) => {
    revision += 1;
    jobId = typeof args?.job_id === 'string' && args.job_id.trim() ? args.job_id : null;
    el('details').hidden = true;
    message('상태를 불러오고 있습니다.', true);
    startWaitTimer();
  };
  app.ontoolresult = (result) => { revision += 1; display(result); };
  app.ontoolcancelled = () => {
    revision += 1;
    clearTimeout(connectionTimer);
    el('details').hidden = true;
    message('상태 조회가 취소되었습니다. 채팅에서 다시 조회할 수 있습니다.');
  };
  button.addEventListener('click', async () => {
    if (!connected || !jobId || busy) return;
    const requestRevision = ++revision;
    el('details').hidden = true;
    message('상태를 새로 확인하고 있습니다.', true);
    let timer;
    try {
      const result = await Promise.race([
        app.callServerTool({ name: 'get_paper_status', arguments: { job_id: jobId } }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), timeoutMs); }),
      ]);
      if (requestRevision === revision) display(result);
    } catch {
      if (requestRevision === revision) message('상태를 확인하지 못했습니다. 채팅에서 다시 조회해 주세요.');
    } finally {
      clearTimeout(timer);
    }
  });
  startWaitTimer();
  Promise.resolve().then(() => app.connect()).then(() => {
    connected = true;
    button.disabled = !jobId || busy;
  }).catch(() => {
    clearTimeout(connectionTimer);
    message('이 화면을 연결하지 못했습니다. 채팅의 도구 응답으로 상태를 확인하세요.');
  });
}
