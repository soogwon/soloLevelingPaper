// 호스트가 제공한 근거 스냅샷만 표시한다. 본문은 HTML로 해석하지 않는다.
export function mountDetailApp(app, doc, timeoutMs = 15000) {
  const el = (id) => doc.getElementById(id);
  const nonempty = (v) => typeof v === 'string' && v.trim().length > 0;
  const optional = (v) => v === null || typeof v === 'string';
  let timer;
  function clear(message, busy = false) {
    clearTimeout(timer);
    el('content').hidden = true;
    el('claims').replaceChildren();
    el('claims-section').hidden = true;
    el('verification').hidden = true;
    el('evidence-list').replaceChildren();
    for (const id of ['answer','verification','original','translation','location','file','metadata','availability','pagination']) el(id).textContent = '';
    el('provenance').replaceChildren();
    el('message').textContent = message;
    doc.querySelector('main').setAttribute('aria-busy', String(busy));
  }
  function wait() {
    clear('호스트 결과를 기다리고 있습니다.', true);
    timer = setTimeout(() => clear('결과가 지연되고 있습니다. 채팅의 응답을 확인하세요.'), timeoutMs);
  }
  function validEvidence(e) {
    return e && ['evidence_id','chunk_id','version_id','parse_revision_id','quote_original'].every(k => nonempty(e[k]))
      && optional(e.translation_revision_id) && optional(e.printed_page_label) && optional(e.quote_ko)
      && Number.isInteger(e.pdf_page) && e.pdf_page > 0
      && (e.section_id === undefined || optional(e.section_id))
      && (e.file_display_name === undefined || typeof e.file_display_name === 'string');
  }
  function display(result) {
    clear('표시할 근거가 없습니다. 채팅의 응답을 확인하세요.');
    if (result?.isError) { el('message').textContent = '근거 조회에 실패했습니다. 채팅의 오류 안내를 확인하세요.'; return; }
    const data = result?.structuredContent;
    const answering = data && Object.hasOwn(data, 'citations');
    const evidence = answering ? data.citations : data?.evidence;
    if (!data || !Array.isArray(evidence)) return;
    const detailed = Object.hasOwn(data, 'summary');
    const kinds = {claim:'핵심 주장',method:'방법',result:'결과',limitation:'한계'};
    const claims = answering ? data.claims : detailed ? (data.summary?.items ?? []) : [];
    const validDetail = !detailed || (
      ['context_id','paper_id','version_id','parse_revision_id','translation_revision_id','scope','file_display_name'].every(k=>nonempty(data[k]))
      && optional(data.title) && Array.isArray(data.section_ids) && data.section_ids.every(nonempty)
      && evidence.every(e=>['version_id','parse_revision_id','translation_revision_id'].every(k=>e?.[k]===data[k]))
      && (data.summary === null || (nonempty(data.summary?.summary_id)
        && data.summary.verification_level === 'structural_only'
        && Number.isInteger(data.summary.total_items) && data.summary.total_items >= claims.length
        && (data.summary.next_offset === null || (Number.isInteger(data.summary.next_offset) && data.summary.next_offset > 0))
        && Array.isArray(claims) && claims.every(c=>Object.hasOwn(kinds,c?.kind)))));

    const ids = new Set(evidence.map(e => e?.evidence_id));
    // 서로 다른 버전의 원문·번역이 한 화면에 섞이지 않도록 연결 조합을 확인한다.
    const revisions = new Set(evidence.map(e => JSON.stringify([e?.version_id,e?.parse_revision_id,e?.translation_revision_id])));
    if (!validDetail || !evidence.every(validEvidence) || ids.size !== evidence.length || revisions.size > 1
      || !Array.isArray(claims) || !claims.every(c => c && nonempty(c.text) && Array.isArray(c.evidence_ids)
        && c.evidence_ids.length && new Set(c.evidence_ids).size === c.evidence_ids.length && c.evidence_ids.every(id => ids.has(id)))
      || (answering && (typeof data.answer_ko !== 'string' || !['ok','partial','insufficient_evidence'].includes(data.status)
        || !['structural_only','model_checked'].includes(data.verification_level)))) {
      el('message').textContent = '근거 응답의 형식 또는 출처 연결을 확인하지 못했습니다. 채팅의 응답을 확인하세요.';
      return;
    }
    el('answer-section').hidden = !(answering || detailed);
    el('claims-section').hidden = !claims.length;
    el('claims-heading').textContent = detailed ? '요약 항목별 근거로 이동' : '주장별 근거로 이동';
    el('verification').hidden = !(answering || (detailed && claims.length));
    el('answer-heading').textContent = detailed ? '저장된 요약' : '반환된 답변';
    if (detailed) {
      el('metadata').textContent = `${data.title || data.file_display_name} · 범위: ${data.scope} · 섹션 ID: ${data.section_ids.join(', ') || '없음'}`;
      el('availability').textContent = '별도로 저장된 키워드·중요 문장은 제공되지 않습니다. 섹션 ID는 제목이 아닙니다.';
      if (claims.length) el('verification').textContent = 'structural_only — 저장 요약의 근거 연결을 확인합니다. 의미 일치 검증은 아닙니다.';
      el('answer').textContent = data.summary ? `저장 요약 총 ${data.summary.total_items}개 항목 중 ${claims.length}개 표시` : '저장된 요약이 없습니다. 자동 생성하지 않습니다.';
      if (data.summary?.next_offset != null) {
        el('pagination').textContent = '다음 요약 항목은 채팅에서 get_paper_detail로 조회하세요: ' + JSON.stringify({context_id:data.context_id,scope:data.scope,summary_id:data.summary.summary_id,offset:data.summary.next_offset});
      }
    }
    if (answering) {
      el('answer').textContent = data.answer_ko;
      el('verification').textContent = `${data.status} · ${data.verification_level} — 검증 수준은 사실성 보증이 아닙니다.`;
    }
    el('message').textContent = evidence.length ? `근거 ${evidence.length}개 · 선택하면 해당 인용 구간으로 이동합니다.` : detailed && data.summary === null ? '저장된 요약이 없어 표시할 근거가 없습니다.' : '반환된 근거가 없습니다.';
    el('content').hidden = false;
    el('detail').hidden = !evidence.length;
    function select(e, focus = false) {
      el('location').textContent = `PDF 기준 ${e.pdf_page}페이지${e.section_id ? ` · 섹션 ID ${e.section_id}` : ''}${e.printed_page_label ? ` · 인쇄 표기 ${e.printed_page_label}` : ''}`;
      el('file').textContent = e.file_display_name ?? '파일명은 이 응답에서 제공되지 않았습니다.';
      el('original').textContent = e.quote_original;
      el('translation').textContent = e.quote_ko?.trim() ? e.quote_ko : '이 근거에 저장된 한국어 번역이 없습니다.';
      el('provenance').replaceChildren();
      for (const [key,label] of [['evidence_id','근거 ID'],['chunk_id','청크 ID'],['version_id','논문 버전'],['parse_revision_id','파싱 리비전'],['translation_revision_id','번역 리비전']]) {
        const dt=doc.createElement('dt'), dd=doc.createElement('dd');
        dt.textContent=label; dd.textContent=e[key] ?? '없음'; el('provenance').append(dt,dd);
      }
      for (const b of el('evidence-list').querySelectorAll('button')) b.setAttribute('aria-pressed',String(b.dataset.id === e.evidence_id));
      if (focus) { el('detail').focus(); el('detail').scrollIntoView?.({block:'nearest'}); }
    }
    function button(e, label) {
      const b=doc.createElement('button'); b.type='button'; b.textContent=label; b.dataset.id=e.evidence_id;
      b.addEventListener('click',()=>select(e,true)); return b;
    }
    evidence.forEach((e,i)=>el('evidence-list').append(button(e,`근거 ${i+1} · PDF ${e.pdf_page}페이지`)));
    for (const c of claims) {
      const li=doc.createElement('li'), text=doc.createElement('p'); text.textContent=detailed ? `${kinds[c.kind]}: ${c.text}` : c.text; li.append(text);
      c.evidence_ids.forEach(id=>{const i=evidence.findIndex(e=>e.evidence_id===id);li.append(button(evidence[i],`근거 ${i+1} 보기`));});
      el('claims').append(li);
    }
    if (evidence.length) select(evidence[0]);
  }
  app.ontoolinput = wait;
  app.ontoolresult = display;
  app.ontoolcancelled = () => clear('조회가 취소되었습니다. 채팅에서 다시 조회할 수 있습니다.');
  wait();
  Promise.resolve().then(()=>app.connect()).catch(()=>clear('화면 연결에 실패했습니다. 채팅의 도구 응답을 확인하세요.'));
}
