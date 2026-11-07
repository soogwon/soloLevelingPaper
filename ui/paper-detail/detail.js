// 호스트가 제공한 근거 스냅샷만 표시한다. 본문은 HTML로 해석하지 않는다.
export function mountDetailApp(app, doc, timeoutMs = 15000) {
  const el = (id) => doc.getElementById(id);
  const nonempty = (v) => typeof v === 'string' && v.trim().length > 0;
  const optional = (v) => v === null || typeof v === 'string';
  let timer;
  let selected;
  const annotationPanel = el('annotations');
  function clear(message, busy = false) {
    clearTimeout(timer);
    selected = null;
    if (annotationPanel) {
      annotationPanel.hidden = true;
      el('note-body').value = '';
      el('records').replaceChildren();
    }
    el('content').hidden = true;
    el('insights')?.replaceChildren();
    el('toc').hidden = true;
    el('toc-items').replaceChildren();
    el('toc-status').textContent = '';
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
  function validInsight(record, data, key) {
    if (!record || record.parse_revision_id !== data.parse_revision_id
      || (record.translation_revision_id !== null && record.translation_revision_id !== data.translation_revision_id)
      || ![record.insight_id, record.text, record.chunk_id, record.quote_original].every(nonempty)
      || (data.scope !== 'paper' && record.section_id !== data.scope)
      || !Number.isSafeInteger(record.pdf_page) || record.pdf_page < 1
      || !Number.isSafeInteger(record.start_offset) || record.start_offset < 0
      || !Number.isSafeInteger(record.end_offset)
      || record.end_offset - record.start_offset !== Array.from(record.quote_original).length) return false;
    if (record.content_kind === 'generated') return ['model','user'].includes(record.producer);
    return record.content_kind === 'extracted' && record.producer === 'extractor'
      && (key === 'keywords' ? record.quote_original.includes(record.text) : record.quote_original === record.text);
  }
  function showInsights(data, panel) {
    const groups = [['keywords', '키워드'], ['important_sentences', '중요 문장']];
    const unavailable = [];
    for (const [key, label] of groups) {
      const records = data[key] === undefined ? [] : data[key];
      if (!Array.isArray(records)) {
        unavailable.push(label + '의 출처를 확인할 수 없습니다.');
        continue;
      }
      if (!records.length) {
        unavailable.push('저장된 ' + label + ' 없음');
        continue;
      }
      const heading = doc.createElement('h2');
      heading.textContent = label;
      const list = doc.createElement('ul');
      for (const record of records) {
        if (!validInsight(record, data, key)) {
          unavailable.push(label + '의 일부 출처를 확인할 수 없습니다.');
          continue;
        }
        const item = doc.createElement('li');
        const source = doc.createElement('blockquote');
        source.textContent = record.quote_original;
        const kind = record.content_kind === 'extracted' ? '원문 추출'
          : record.producer === 'model' ? 'AI 생성' : '사용자 작성';
        item.textContent = `${kind} · ${record.text} · PDF ${record.pdf_page}페이지`;
        const provenance = doc.createElement('details');
        const summary = doc.createElement('summary');
        summary.textContent = '원문 출처 정보';
        const info = doc.createElement('p');
        info.textContent = [
          `청크 ${record.chunk_id} · 원문 구간 [${record.start_offset}, ${record.end_offset})`,
          `파싱 리비전 ${record.parse_revision_id}`,
          record.translation_revision_id && `번역 리비전 ${record.translation_revision_id}`,
          record.producer_version && `작성 버전 ${record.producer_version}`,
          record.collection_created_at && `저장 시각 ${record.collection_created_at}`,
        ].filter(Boolean).join(' · ');
        provenance.append(summary, info);
        item.append(source, provenance);
        list.append(item);
      }
      if (list.children.length) panel.append(heading, list);
    }
    return [...new Set(unavailable)].join(' · ');
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
    const catalog = data.section_catalog === undefined ? [] : data.section_catalog;
    const validCatalog = Array.isArray(catalog) && catalog.every(s => s
      && s.parse_revision_id === data.parse_revision_id && nonempty(s.section_id) && nonempty(s.title)
      && (s.parent_section_id === null || nonempty(s.parent_section_id))
      && Number.isSafeInteger(s.level) && s.level > 0 && Number.isSafeInteger(s.ordinal) && s.ordinal > 0
      && Number.isSafeInteger(s.start_pdf_page) && s.start_pdf_page > 0
      && Number.isSafeInteger(s.end_pdf_page) && s.end_pdf_page >= s.start_pdf_page);
    const validDetail = !detailed || (
      ['context_id','paper_id','version_id','parse_revision_id','translation_revision_id','scope','file_display_name'].every(k=>nonempty(data[k]))
      && validCatalog && optional(data.title) && Array.isArray(data.section_ids) && data.section_ids.every(nonempty)
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
    const titles = new Map();
    if (detailed) {
      for (const section of catalog) titles.set(section.section_id, section.title);
      el('toc').hidden = false;
      el('toc-status').textContent = catalog.length ? '' : '이 파싱 리비전에 저장된 목차가 없습니다.';
      for (const section of catalog) {
        const item = doc.createElement('li');
        const parent = titles.get(section.parent_section_id);
        const pages = section.start_pdf_page === section.end_pdf_page
          ? section.start_pdf_page : `${section.start_pdf_page}–${section.end_pdf_page}`;
        item.textContent = `${section.title} · PDF ${pages}페이지` + (parent ? ` · 상위 섹션: ${parent}` : '');
        el('toc-items').append(item);
      }
      el('metadata').textContent = `${data.title || data.file_display_name} · 범위: ${data.scope} · 섹션 ID: ${data.section_ids.join(', ') || '없음'}`;
      const insights = el('insights');
      el('availability').textContent = insights ? showInsights(data, insights)
        : '키워드·중요 문장은 논문 상세 카드를 열어 확인하세요.';
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
    const uncertain = new Set();
    const pending = new Set();
    const loading = new Set();
    function showRecords(e) {
      const records = el('records');
      records.replaceChildren();
      for (const record of e.annotations) {
        const li = doc.createElement('li');
        li.textContent = record.kind === 'note' ? '메모 · ' + record.body
          : '하이라이트 · ' + (record.body || e.quote_original);
        records.append(li);
      }
      el('more-records').hidden = !e.annotation_cursor;
      el('more-records').disabled = loading.has(e) || pending.has(e);
      el('editor').disabled = uncertain.has(e) || e.annotation_start == null || pending.has(e) || loading.has(e);
      el('record-status').textContent = uncertain.has(e) ? '저장 결과가 확인되지 않았습니다. 상세 카드를 다시 조회해 기록을 확인하세요.'
        : e.annotation_start == null ? '원문 위치를 확정할 수 없어 작성할 수 없습니다.'
        : pending.has(e) ? '저장 중입니다.' : records.children.length ? `기록 ${records.children.length}건 표시 · 전체 ${e.annotation_count ?? records.children.length}건` : '저장된 기록이 없습니다.';
    }
    async function loadMore(e) {
      if (selected !== e || loading.has(e) || !e.annotation_cursor) return;
      loading.add(e);
      showRecords(e);
      try {
        const result = await app.callServerTool({name: 'list_evidence_annotations', arguments: {
          context_id: data.context_id, evidence_id: e.evidence_id, cursor: e.annotation_cursor,
        }});
        const page = result?.structuredContent;
        if (result?.isError || page?.context_id !== data.context_id || page.evidence_id !== e.evidence_id
            || page.annotation_start !== e.annotation_start || !Array.isArray(page.annotations)) throw new Error();
        e.annotations.push(...page.annotations);
        e.annotation_count = page.annotation_count;
        e.annotation_cursor = page.annotation_cursor;
      } catch {
        if (selected === e) el('record-status').textContent = '기록을 불러오지 못했습니다. 다시 시도하세요.';
        return;
      } finally {
        loading.delete(e);
        if (selected === e) {
          el('more-records').disabled = pending.has(e);
          el('editor').disabled = uncertain.has(e) || pending.has(e) || e.annotation_start == null;
        }
      }
      if (selected === e) showRecords(e);
    }
    async function save(e, kind) {
      if (selected !== e || uncertain.has(e) || loading.has(e) || pending.has(e) || !Number.isSafeInteger(e.annotation_start)) return;
      const body = kind === 'note' ? el('note-body').value : '';
      if (kind === 'note' && !body.trim()) {
        el('record-status').textContent = '메모 내용을 입력하세요.';
        return;
      }
      pending.add(e);
      showRecords(e);
      try {
        const result = await app.callServerTool({name: 'create_annotation', arguments: {request: {
          context_id: data.context_id, kind, body, segments: [{chunk_id: e.chunk_id,
            start_offset: e.annotation_start, end_offset: e.annotation_start + Array.from(e.quote_original).length,
            quote_snapshot: e.quote_original}],
        }}});
        const saved = result?.structuredContent;
        const record = saved?.annotation;
        if (result?.isError || saved?.outcome !== 'saved' || record?.context_id !== data.context_id
            || record.kind !== kind || record.body !== body || !nonempty(record.annotation_id)) throw new Error();
        e.annotation_count = (e.annotation_count ?? e.annotations.length) + 1;
        e.annotations.unshift(record);
        if (selected === e && kind === 'note') el('note-body').value = '';
      } catch {
        uncertain.add(e);
        pending.delete(e);
        if (selected === e) showRecords(e);
        return;
      }
      pending.delete(e);
      if (selected === e) showRecords(e);
    }
    function select(e, focus = false) {
      selected = e;
      if (annotationPanel) {
        el('note-body').value = '';
        annotationPanel.hidden = !(detailed && Array.isArray(e.annotations));
      }
      if (annotationPanel && !annotationPanel.hidden) {
        el('more-records').onclick = () => loadMore(e);
        showRecords(e);
        el('save-note').onclick = () => save(e, 'note');
        el('save-highlight').onclick = () => save(e, 'highlight');
      }
      el('location').textContent = `PDF 기준 ${e.pdf_page}페이지${titles.has(e.section_id) ? ` · ${titles.get(e.section_id)}` : e.section_id ? ` · 섹션 ID ${e.section_id}` : ''}${e.printed_page_label ? ` · 인쇄 표기 ${e.printed_page_label}` : ''}`;
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
