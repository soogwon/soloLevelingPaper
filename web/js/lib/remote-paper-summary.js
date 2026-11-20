import { request } from '../api/client.js';
import { html, setHTML } from './html.js';

// 로컬 동기화 요약과 원격 실행 요약을 합치지 않고 고정 맥락별로 표시한다.
export async function remotePaperSummary(view, paper, version) {
  const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
  if (!view || !uuid(paper) || typeof version !== 'string' || !version.trim() || version.length > 128) return;
  let closed=false, seq=0, busy=false, contexts=[], next=null, selected=null, summary=null;
  const events=['auth:logout','auth:expired','hashchange','pagehide'];
  const close=()=>{closed=true;++seq;summary=null;contexts=[];view.textContent='';events.forEach(e=>window.removeEventListener(e,close));};
  events.forEach(e=>window.addEventListener(e,close));
  const current=n=>!closed && view.isConnected && seq===n;
  function draw(message='') {
    if(closed || !view.isConnected)return;
    setHTML(view,html`<h2>원격 저장 요약</h2><p role="status">${message}</p>
      <p>현재 판본의 최신 성공 요약입니다. 맥락별 결과를 조회하며 새 요약을 생성하지 않습니다.</p>
      ${contexts.map(c=>html`<button data-summary-context="${c.context_id}" ${busy?'disabled':''} aria-pressed="${String(c.context_id===selected)}">학습 맥락 ${c.context_id}</button>`)}
      ${next?html`<button data-summary-more ${busy?'disabled':''}>학습 맥락 더 보기</button>`:''}
      ${summary?html`<p>범위: ${summary.scope==='paper'?'논문 전체':`섹션 ${summary.section_id}`} · 판본: ${summary.binding.server_version_id}</p>
        ${summary.source_count < summary.total_scope_chunks ? html`<p>선택 근거 ${summary.source_count} / ${summary.total_scope_chunks}</p>` : ''}
        <p>게시: ${summary.binding.publication_id} · 생성: ${summary.generated_at}</p>
        <p>구조·근거 ID 검증 결과이며 의미적 정확성은 별도 확인이 필요합니다.</p>
        ${summary.items.map(i=>html`<section><h3>${({claim:'핵심 주장',method:'방법',result:'결과',limitation:'한계'})[i.kind]}</h3><p>${i.text}</p><p>근거 ID: ${i.evidence_ids.join(', ')}</p></section>`)}`:''}
      <button data-summary-refresh ${busy?'disabled':''}>원격 요약 다시 조회</button>`);
    view.querySelectorAll('[data-summary-context]').forEach(e=>{e.onclick=()=>loadSummary(e.dataset.summaryContext);});
    const bind=(s,fn)=>{const e=view.querySelector(s);if(e)e.onclick=fn;};
    bind('[data-summary-more]',()=>loadContexts(true));bind('[data-summary-refresh]',()=>loadContexts(false));
  }
  async function loadContexts(append) {
    if(busy || closed)return;
    busy=true;const n=++seq;summary=null;draw('조회 중입니다.');
    try {
      const page=await request('GET',`/remote-summaries/papers/${paper}/contexts`,{query:{version_id:version,after:append?next:undefined}});
      if(!current(n))return;
      if(!Array.isArray(page.items) || page.items.length>20 || page.items.some(c=>!uuid(c.context_id)||!uuid(c.publication_id)||c.paper_id!==paper||c.server_version_id!==version)
        || (page.next_cursor!=null && (!uuid(page.next_cursor)||append && page.next_cursor===next)))throw Error('scope');
      contexts=append?[...new Map([...contexts,...page.items].map(c=>[c.context_id,c])).values()]:page.items;next=page.next_cursor;busy=false;
      if(!append){selected=null;if(contexts.length){await loadSummary(contexts[0].context_id);return;}}
      draw(contexts.length?'요약을 볼 학습 맥락을 선택하세요.':'이 판본에는 저장된 원격 요약이 없습니다.');
    } catch {if(current(n)){busy=false;contexts=[];next=null;selected=null;summary=null;draw('요약을 조회하지 못했습니다. 로그인과 기능 활성화를 확인하고 다시 조회하세요.');}}
  }
  async function loadSummary(context) {
    const expected=contexts.find(c=>c.context_id===context);if(busy||closed||!expected)return;
    busy=true;const n=++seq;selected=context;summary=null;draw('조회 중입니다.');
    try {
      const value=await request('GET',`/remote-summaries/contexts/${context}/latest`);
      if(!current(n))return;
      const r=value.result,b=r?.binding;
      if(value.state!=='completed'||value.context_id!==context||!uuid(value.request_key)||r?.context_id!==context||b?.context_id!==context
        ||b.paper_id!==paper||b.server_version_id!==version||b.publication_id!==expected.publication_id
        ||!['paper','section'].includes(r.scope)||(r.scope==='section'&&typeof r.section_id!=='string')
        ||!Array.isArray(r.items)||!r.items.length||r.items.length>100||r.items.some(i=>!['claim','method','result','limitation'].includes(i?.kind)||typeof i.text!=='string'||!Array.isArray(i.evidence_ids)||!i.evidence_ids.length||!i.evidence_ids.every(uuid)))throw Error('scope');
      summary=r;busy=false;draw();
    } catch {if(current(n)){busy=false;summary=null;draw('저장 요약을 조회하지 못했습니다. 다시 조회해 주세요.');}}
  }
  await loadContexts(false);
}
