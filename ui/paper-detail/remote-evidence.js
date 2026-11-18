const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
export function mountRemoteEvidence(app, doc) {
  const el = id => doc.getElementById(id);
  let generation = 0, scope = null, incoming = null, busy = false, items = [], cursor = null;
  function clear(message) {
    ++generation; scope = null; items = []; cursor = null; busy = false;
    el('evidence').textContent = ''; el('records').textContent = '';
    el('status').textContent = message;
    el('more').disabled = true; el('refresh').disabled = true;
  }
  function render(reply, expected, append = false) {
    const r = reply?.structuredContent?.result, e = r?.evidence, p = r?.annotation_page;
    if (reply?.isError || reply?.structuredContent?.status !== 'ok'
        || !uuid(r?.context_id) || !uuid(r?.evidence_id) || !/^[a-f0-9]{64}$/.test(r?.binding ?? '')
        || r.context_id !== expected.context_id || r.evidence_id !== expected.evidence_id
        || (expected.binding && r.binding !== expected.binding)
        || (r.after ?? null) !== (expected.after ?? null)
        || e?.context_id !== r.context_id || e?.evidence_id !== r.evidence_id
        || !uuid(e?.chunk_id) || !uuid(e?.parse_revision_id)
        || !uuid(e.version_id) || !uuid(e.publication_id) || !uuid(e.embedding_set_id)
        || !/^[a-f0-9]{64}$/.test(e.original_text_sha256 ?? '')
        || !Number.isSafeInteger(e.pdf_page) || e.pdf_page < 1
        || typeof e.quote_original !== 'string' || typeof e.quote_ko !== 'string'
        || typeof e.page_notice !== 'string' || !Array.isArray(p?.items) || p.items.length > 20
        || !(p.next_cursor === null || uuid(p.next_cursor))
        || !p.items.every(a => uuid(a.annotation_id) && a.context_id === r.context_id
          && typeof a.body === 'string' && ['note','highlight'].includes(a.kind)
          && Number.isSafeInteger(a.revision) && a.revision > 0
          && Array.isArray(a.segments) && a.segments.every(s => s && typeof s.quote_snapshot === 'string' && ['original','translation'].includes(s.target)) && a.segments.some(s => s.chunk_id === e.chunk_id
            && s.parse_revision_id === e.parse_revision_id && (s.target === 'original'
              || (s.target === 'translation' && s.translation_revision_id === e.translation_revision_id))))
        || (p.next_cursor !== null && (p.items.at(-1)?.annotation_id !== p.next_cursor || p.next_cursor === expected.after))) {
      clear('결과나 연결을 확인할 수 없습니다. 채팅에서 상세를 다시 여세요.'); return;
    }
    const previous = append ? items : [], ids = new Set(previous.map(a => a.annotation_id));
    if (p.items.some(a => ids.has(a.annotation_id) || !ids.add(a.annotation_id))) {
      clear('중복된 페이지입니다. 채팅에서 상세를 다시 여세요.'); return;
    }
    scope = {context_id:r.context_id, evidence_id:r.evidence_id, binding:r.binding};
    items = [...previous, ...p.items]; cursor = p.next_cursor; busy = false;
    el('evidence').textContent = `원문\n${e.quote_original}\n\n번역\n${e.quote_ko}\n\nPDF 페이지: ${e.pdf_page}\n인쇄 페이지: ${e.printed_page_label ?? '없음'}\n판본: ${e.version_id}\n파싱: ${e.parse_revision_id}\n번역 리비전: ${e.translation_revision_id}\n게시: ${e.publication_id}\n${e.page_notice}`;
    el('records').textContent = items.length ? items.map(a => `${a.kind} · revision ${a.revision}\n${a.body}\n` + a.segments.map(s => `${s.target === 'translation' ? '번역' : '원문'}: ${s.quote_snapshot}`).join('\n')).join('\n\n') : '이 근거에 연결된 기록이 없습니다.';
    el('status').textContent = '같은 고정 청크에 연결된 기록입니다. 인용문과 정확히 겹치는 구간만의 목록은 아닙니다.';
    el('more').disabled = cursor === null; el('refresh').disabled = false;
  }
  async function load(append) {
    if (!scope || busy || (append && !cursor)) return;
    const expected = {...scope, after:append ? cursor : null}, current = ++generation;
    busy = true; el('more').disabled = true; el('refresh').disabled = true;
    el('status').textContent = '저장된 근거와 기록을 조회하는 중입니다.';
    try {
      const reply = await app.callServerTool({name:'show_remote_evidence_detail', arguments:{context_id:expected.context_id, evidence_id:expected.evidence_id, after:expected.after, expected_binding:expected.binding}});
      if (current === generation) render(reply, expected, append);
    } catch { if (current === generation) clear('조회하지 못했습니다. 채팅에서 상세를 다시 여세요.'); }
  }
  el('more').onclick = () => load(true); el('refresh').onclick = () => load(false);
  app.ontoolinput = event => {clear('저장 근거를 조회하는 중입니다.'); incoming = event?.arguments ?? null;};
  app.ontoolresult = reply => { const expected = incoming; incoming = null; if (expected) render(reply, expected); };
  app.ontoolcancelled = () => {incoming = null; clear('조회가 취소됐습니다.');};
  doc.defaultView?.addEventListener('pagehide', () => {incoming = null; clear('카드를 다시 열어 주세요.');});
  app.connect().catch(() => clear('카드를 사용할 수 없습니다. 채팅에서 근거를 조회하세요.'));
}
