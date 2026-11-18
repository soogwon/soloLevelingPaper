const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
export function mountRemotePaper(app, doc) {
  const el = id => doc.getElementById(id);
  const buttons = ['sections-more','records-more','refresh'];
  let generation = 0, incoming = null, scope = null, busy = false;
  function clear(message) {
    ++generation; scope = null; busy = false;
    for (const id of ['paper','sections','records']) el(id).textContent = '';
    for (const id of buttons) el(id).disabled = true;
    el('status').textContent = message;
  }
  function render(reply, expected) {
    const r = reply?.structuredContent?.result, d = r?.detail, p = r?.annotation_page;
    if (reply?.isError || reply?.structuredContent?.status !== 'ok'
        || !uuid(r?.context_id) || r.context_id !== expected.context_id || d?.context_id !== r.context_id
        || !/^[a-f0-9]{64}$/.test(r?.binding ?? '') || (expected.expected_binding && r.binding !== expected.expected_binding)
        || r.section_offset !== (expected.section_offset ?? 0) || (r.after ?? null) !== (expected.after ?? null)
        || !uuid(d?.publication_id) || !uuid(d?.parse_revision_id) || !uuid(d?.embedding_set_id)
        || !uuid(d?.paper_id) || typeof d?.version_id !== 'string' || !d.version_id
        || (expected.publication_id && d.publication_id !== expected.publication_id)
        || !Array.isArray(d?.section_catalog) || d.section_catalog.length > 20
        || !d.section_catalog.every(s => s && typeof s.title === 'string' && typeof s.section_id === 'string'
          && s.parse_revision_id === d.parse_revision_id && Number.isSafeInteger(s.start_pdf_page)
          && Number.isSafeInteger(s.end_pdf_page) && s.start_pdf_page > 0 && s.end_pdf_page >= s.start_pdf_page)
        || !(d.next_section_offset === null || d.next_section_offset === r.section_offset + 20)
        || !Array.isArray(p?.items) || p.items.length > 20
        || !p.items.every(a => a && uuid(a.annotation_id) && a.context_id === r.context_id
          && typeof a.body === 'string' && ['note','highlight'].includes(a.kind)
          && Number.isSafeInteger(a.revision) && a.revision > 0
          && Array.isArray(a.segments) && a.segments.every(s => s && ['original','translation'].includes(s.target) && typeof s.quote_snapshot === 'string'))
        || new Set(p.items.map(a=>a.annotation_id)).size !== p.items.length
        || !(p.next_cursor === null || (uuid(p.next_cursor) && p.next_cursor !== r.after && p.items.at(-1)?.annotation_id === p.next_cursor))
        || d.summary !== null || !Array.isArray(d.keywords) || d.keywords.length
        || !Array.isArray(d.important_sentences) || d.important_sentences.length
        || typeof d.generation_notice !== 'string' || typeof d.page_notice !== 'string') {
      clear('결과나 연결을 확인할 수 없습니다. 채팅에서 상세를 다시 여세요.'); return;
    }
    scope = {context_id:r.context_id, expected_binding:r.binding, publication_id:d.publication_id,
      section_offset:r.section_offset, after:r.after, nextSection:d.next_section_offset, nextRecord:p.next_cursor};
    busy = false;
    el('paper').textContent = `${d.title ?? '제목 없음'}\n판본: ${d.version_id}\n게시: ${d.publication_id}\n${d.page_notice}\n\n${d.generation_notice}`;
    el('sections').textContent = d.section_catalog.length ? d.section_catalog.map(s=>`${s.title} · PDF ${s.start_pdf_page}–${s.end_pdf_page}페이지`).join('\n') : '저장된 목차가 없습니다.';
    el('records').textContent = p.items.length ? p.items.map(a=>`${a.kind} · revision ${a.revision}\n${a.body}\n${a.segments.map(s=>`${s.target === 'translation' ? '번역' : '원문'}: ${s.quote_snapshot}`).join('\n')}`).join('\n\n') : '저장된 기록이 없습니다.';
    el('status').textContent = '현재 페이지의 기록입니다. 근거에 연결되지 않은 메모도 포함합니다.';
    el('sections-more').disabled = scope.nextSection === null;
    el('records-more').disabled = scope.nextRecord === null;
    el('refresh').disabled = false;
  }
  async function load(kind) {
    if (!scope || busy || (kind === 'sections' && scope.nextSection === null) || (kind === 'records' && scope.nextRecord === null)) return;
    const expected = {...scope, section_offset:kind === 'refresh' ? 0 : kind === 'sections' ? scope.nextSection : scope.section_offset,
      after:kind === 'refresh' ? null : kind === 'records' ? scope.nextRecord : scope.after};
    const current = ++generation;
    busy = true; for (const id of buttons) el(id).disabled = true;
    try {
      const {context_id, expected_binding, section_offset, after} = expected;
      const reply = await app.callServerTool({name:'show_remote_paper_detail',arguments:{context_id, expected_binding, section_offset, after}});
      if (current === generation) render(reply, expected);
    } catch { if (current === generation) clear('조회하지 못했습니다. 채팅에서 상세를 다시 여세요.'); }
  }
  el('sections-more').onclick = () => load('sections');
  el('records-more').onclick = () => load('records');
  el('refresh').onclick = () => load('refresh');
  app.ontoolinput = e => {clear('저장된 상세를 조회합니다.'); incoming = e?.arguments ?? null;};
  app.ontoolresult = r => {const expected = incoming; incoming = null; if (expected) render(r, expected);};
  app.ontoolcancelled = () => {incoming = null; clear('조회가 취소됐습니다.');};
  doc.defaultView?.addEventListener('pagehide', () => {incoming = null; clear('카드를 다시 열어 주세요.');});
  app.connect().catch(() => clear('카드를 사용할 수 없습니다. 채팅에서 상세를 조회하세요.'));
}
