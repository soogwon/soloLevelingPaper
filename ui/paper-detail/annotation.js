export function mountAnnotation(app, doc) {
  const el = id => doc.getElementById(id);
  let generation = 0;
  function clearSource() {
    el('source').hidden = true;
    el('excerpt').replaceChildren();
  }
  function reset(message) {
    ++generation;
    clearSource();
    el('title').textContent = '저장 기록';
    el('body').textContent = '';
    el('segments').replaceChildren();
    el('status').textContent = message;
  }
  function render(result, selected) {
    const data = result?.structuredContent;
    const record = data?.annotation;
    reset('기록을 조회할 수 없습니다.');
    if (result?.isError || !Array.isArray(record?.segments)
        || !Array.isArray(data.segment_available) || data.segment_available.length !== record.segments.length) return;
    el('title').textContent = record.kind === 'highlight' ? '하이라이트' : '메모';
    el('body').textContent = record.body;
    el('status').textContent = record.segments.length ? '' : '연결된 원문 구간이 없습니다.';
    for (const [i, segment] of record.segments.entries()) {
      const row = doc.createElement('li');
      const quote = doc.createElement('p');
      quote.textContent = (segment.target === 'translation' ? '번역문 · ' : '원문 · ') + segment.quote_snapshot;
      row.append(quote);
      if (data.segment_available[i] === true) {
        const button = doc.createElement('button');
        button.textContent = segment.target === 'translation' ? '저장 번역문 보기' : '원문 보기';
        button.onclick = () => open(record, segment.position);
        row.append(button);
      } else {
        row.append('현재 저장된 대상에서 이 구간을 찾을 수 없습니다.');
      }
      el('segments').append(row);
    }
    const source = data.source;
    if (!source || (selected !== undefined && source.position !== selected)) return;
    const i = record.segments.findIndex(s => s.position === source.position && s.quote_snapshot === source.quote);
    if (i < 0 || data.segment_available[i] !== true) return;
    const label = record.segments[i].target === 'translation' ? '저장 번역문' : '원문';
    el('location').textContent = source.pdf_page ? `PDF ${source.pdf_page}페이지 · ${label}` : label;
    const mark = doc.createElement('mark');
    mark.textContent = source.quote;
    el('excerpt').append(source.before, mark, source.after);
    el('source').hidden = false;
    el('source').focus();
  }
  async function open(record, position) {
    const current = ++generation;
    clearSource();
    el('status').textContent = '현재 원문을 확인하는 중입니다.';
    try {
      const result = await app.callServerTool({name: 'get_annotation', arguments: {
        request: {context_id: record.context_id, annotation_id: record.annotation_id, source_position: position},
      }});
      if (current !== generation) return;
      const returned = result?.structuredContent?.annotation;
      if (returned?.context_id !== record.context_id || returned?.annotation_id !== record.annotation_id)
        throw new Error();
      render(result, position);
    } catch {
      if (current === generation) reset('원문을 조회할 수 없습니다. 메모를 다시 조회해 주세요.');
    }
  }
  app.ontoolinput = () => reset('기록을 불러오는 중입니다.');
  app.ontoolresult = result => render(result);
  app.ontoolcancelled = () => reset('조회가 취소됐습니다.');
  app.connect().catch(() => reset('카드를 열 수 없습니다. 채팅에서 메모를 조회해 주세요.'));
}
