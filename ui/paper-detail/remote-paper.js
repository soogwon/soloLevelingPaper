import { remoteRecordText } from './remote-record-text.js';
const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
export function mountRemotePaper(app, doc) {
  const el = id => doc.getElementById(id);
  const buttons = ['sections-more','records-more','refresh'];
  let summaryMarkdown = null;
  let generation = 0, incoming = null, scope = null, busy = false;
  function clear(message) {
    ++generation; scope = null; busy = false; resetSummary('요약 조회 전입니다.');
    for (const id of ['paper','sections','records','insights']) el(id).textContent = '';
    for (const id of buttons) el(id).disabled = true;
    el('status').textContent = message;
  }

  function resetSummary(message) {
    summaryMarkdown = null;
    el('summary').textContent = message;
    el('summary-export').disabled = true;
    el('summary-markdown').value = ''; el('summary-markdown').hidden = true;
  }
  function displaySummary(value, markdown, expected) {
    resetSummary('저장 요약을 확인합니다.');
    try {
      const result = value?.result, b = result?.binding;
      if (value?.status === 'unsupported') {resetSummary('이 연결에서는 저장 요약 조회를 지원하지 않습니다.'); return;}
      if (value?.status === 'not_found') {resetSummary('저장된 요약이 없습니다. 자동 생성하지 않습니다.'); return;}
      if (value?.status === 'not_authorized') {resetSummary('요약 조회 권한을 확인해 주세요.'); return;}
      if (value?.state !== 'completed' || value.context_id !== expected.context_id
          || !uuid(value.request_key) || result?.context_id !== expected.context_id || b?.context_id !== expected.context_id
          || b.publication_id !== expected.publication_id || b.parse_revision_id !== expected.parse_revision_id
          || b.server_version_id !== expected.version_id || b.paper_id !== expected.paper_id
          || b.translation_revision_id !== expected.translation_revision_id
          || !['paper','section'].includes(result.scope) || (result.scope === 'section' && typeof result.section_id !== 'string')
          || !Array.isArray(result.items) || !result.items.length || result.items.length > 100
          || !result.items.every(i=>['claim','method','result','limitation'].includes(i?.kind) && typeof i.text === 'string'
            && Array.isArray(i.evidence_ids) && i.evidence_ids.length && i.evidence_ids.every(uuid))) throw new Error();
      const brief = result.prompt_version === 'remote-summary-v2';
      const range = result.scope === 'paper' ? '논문 전체' : '섹션 ' + result.section_id;
      const sampled = brief && result.source_count < result.total_scope_chunks
        ? `\n선택 근거: ${result.source_count} / ${result.total_scope_chunks}` : '';
      const formatItem = i => {
        const pages = [...new Set(i.evidence_ids.map(id => result.evidence_pages?.[id])
          .filter(page => Number.isSafeInteger(page) && page > 0))].sort((a, b) => a - b);
        return `${i.text}\n${pages.length ? `PDF ${pages.join(', ')}쪽 · ` : ''}근거 ID: ${i.evidence_ids.join(', ')}`;
      };
      const items = brief ? Object.entries({claim: '연구 질문', method: '핵심 접근', result: '주요 결과'})
        .map(([kind, label]) => {
          const item = result.items.find(i => i.kind === kind);
          return `${label}: ${item ? formatItem(item) : '요약에 포함된 근거가 없어요.'}`;
        }) : result.items.map(i => `${i.kind}: ${formatItem(i)}`);
      el('summary').textContent = `범위: ${range}${sampled}\n판본: ${b.server_version_id}\n생성: ${result.generated_at}\n구조·근거 ID 검증 결과이며 의미적 정확성은 별도 확인이 필요합니다.\n\n`
        + items.join('\n\n');
      if (markdown?.status === 'ok' && markdown.context_id === expected.context_id
          && markdown.request_key === value.request_key && markdown.publication_id === expected.publication_id
          && markdown.media_type === 'text/markdown' && typeof markdown.markdown === 'string'
          && markdown.markdown.length > 0) {
        summaryMarkdown = markdown.markdown;
        el('summary-export').disabled = false;
      }
    } catch {resetSummary('요약을 조회하지 못했습니다. 새로고침하거나 채팅에서 같은 맥락의 요약을 조회하세요.');}
  }
  el('summary-export').onclick = () => {
    if (!scope || !summaryMarkdown) return;
    el('summary-markdown').value = summaryMarkdown;
    el('summary-markdown').hidden = false;
  };

  function displayInsights(detail) {
    const rows = [...detail.keywords, ...detail.important_sentences].filter(item =>
      item?.parse_revision_id === detail.parse_revision_id &&
      (item.translation_revision_id === null || item.translation_revision_id === detail.translation_revision_id) &&
      ['extracted', 'generated'].includes(item.content_kind) &&
      Number.isSafeInteger(item.pdf_page) && item.pdf_page > 0 &&
      typeof item.text === 'string' && typeof item.quote_original === 'string');
    const messages = {
      empty: '이관된 키워드·중요 문장이 없습니다.',
      unsupported: '이 연결에서는 키워드·중요 문장 조회를 지원하지 않습니다.',
      unavailable: '키워드·중요 문장을 조회하지 못했습니다.',
    };
    const display = rows.map(item =>
      `${item.kind === 'keyword' ? '키워드' : '중요 문장'} · ${item.content_kind === 'extracted' ? '원문 추출' : '기존 생성'} · PDF ${item.pdf_page}페이지
${item.text}
원문 인용: ${item.quote_original}`);
    if (detail.insights_truncated) display.push('저장된 항목 중 처음 40개를 표시합니다.');
    el('insights').textContent = display.length ? display.join('\n\n')
      : messages[detail.insights_status ?? 'unsupported'] ?? '고정 판본과 일치하는 항목이 없습니다.';
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
        || d.summary !== null || !Array.isArray(d.keywords) || d.keywords.length > 40
        || !Array.isArray(d.important_sentences) || d.important_sentences.length + d.keywords.length > 40
        || typeof d.generation_notice !== 'string' || typeof d.page_notice !== 'string') {
      clear('결과나 연결을 확인할 수 없습니다. 채팅에서 상세를 다시 여세요.'); return;
    }
    scope = {context_id:r.context_id, expected_binding:r.binding, publication_id:d.publication_id,
      section_offset:r.section_offset, after:r.after, nextSection:d.next_section_offset, nextRecord:p.next_cursor};
    busy = false;
    el('paper').textContent = `${d.title ?? '제목 없음'}\n판본: ${d.version_id}\n게시: ${d.publication_id}\n${d.page_notice}\n\n${d.generation_notice}`;
    el('sections').textContent = d.section_catalog.length ? d.section_catalog.map(s=>`${s.title} · PDF ${s.start_pdf_page}–${s.end_pdf_page}페이지`).join('\n') : '저장된 목차가 없습니다.';
    el('records').textContent = p.items.length ? p.items.map(remoteRecordText).join('\n\n') : '저장된 기록이 없습니다.';
    el('status').textContent = '현재 페이지의 기록입니다. 근거에 연결되지 않은 메모도 포함합니다.';
    el('sections-more').disabled = scope.nextSection === null;
    el('records-more').disabled = scope.nextRecord === null;
    el('refresh').disabled = false;
    displaySummary(r.saved_summary, r.saved_summary_markdown, d);
    displayInsights(d);
  }
  async function load(kind) {
    if (!scope || busy || (kind === 'sections' && scope.nextSection === null) || (kind === 'records' && scope.nextRecord === null)) return;
    const expected = {...scope, section_offset:kind === 'refresh' ? 0 : kind === 'sections' ? scope.nextSection : scope.section_offset,
      after:kind === 'refresh' ? null : kind === 'records' ? scope.nextRecord : scope.after};
    const current = ++generation;
    resetSummary('상세를 조회합니다.');
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
