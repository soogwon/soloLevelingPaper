const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
const canonical = v => JSON.stringify(v, function(k,x) {return x && typeof x==='object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])) : x;});
export function mountRemoteCompose(app,doc,crypto=doc.defaultView.crypto) {
 const el=id=>doc.getElementById(id);
 let generation=0, incoming=null, scope=null, key=null, record=null, busy=false;
 function reset(message) {
  ++generation; incoming=null;scope=null;key=null;record=null;busy=false;
  el('new').checked=false;for(const id of ['new','save','lookup','confirm'])el(id).disabled=true;
  el('input').textContent='';el('record').textContent='';el('status').textContent=message;
 }
 function message(text) {el('status').textContent=text;}
 function showRecord(result, state='committed') {
  record=null;el('record').textContent='';el('confirm').disabled=true;
  const a=result?.annotation;
  if (result?.request_key!==key || !uuid(a?.annotation_id) || a.context_id!==scope.context_id
      || a.kind!==scope.value.kind || !Number.isSafeInteger(a.revision) || a.revision<1 || !['note','highlight'].includes(a.kind)
      || typeof a.body!=='string' || !Array.isArray(a.segments)) {message('저장 결과가 불명확합니다. 같은 요청으로 다시 조회하세요.');return;}
  if(state==='deleted'){message('삭제된 기록입니다. 새 키로 재생성하지 않습니다.');return;}
  if(state!=='committed'||!a.segments.every(s=>s&&['original','translation'].includes(s.target)&&typeof s.quote_snapshot==='string')){message('기록을 확인할 수 없습니다.');return;}
  record=a;el('record').textContent=`${a.kind} · ${a.annotation_id} · revision ${a.revision}\n${a.body}\n`+a.segments.map(s=>`${s.target==='translation'?'번역':'원문'}: ${s.quote_snapshot}`).join('\n');
  el('confirm').disabled=false;message('기존 저장 결과입니다. 내용을 확인한 뒤 수신을 확정하세요.');
 }
 async function send(name,args,accept) {
  if(!scope||busy)return;
  const current=generation;busy=true;
  for(const id of ['new','save','lookup','confirm'])el(id).disabled=true;
  try {const r=await app.callServerTool({name,arguments:args});if(current!==generation)return;
   if(r?.isError||r?.structuredContent?.status!=='ok'){record=null;el('record').textContent='';message('저장·확인을 완료하지 못했습니다. 기존 요청을 조회하거나 인증 웹의 저장 동의를 확인하세요.');}
   else accept(r.structuredContent.result);
  }catch{if(current===generation){record=null;el('record').textContent='';message('응답이 유실됐을 수 있습니다. 새 키로 저장하지 말고 기존 요청을 조회하세요.');}}
  finally{if(current===generation){busy=false;el('lookup').disabled=!key;}}
 }
 el('new').onchange=()=>{el('save').disabled=!scope||!!key||busy||!el('new').checked;};
 el('save').onclick=async()=>{
  if(!scope||key||busy||!el('new').checked)return;
  try{key=crypto.randomUUID();if(!uuid(key))throw Error();}catch{reset('요청 키를 준비할 수 없습니다. 저장하지 않았습니다.');return;}
  el('new').checked=false;
  await send('save_remote_annotation_card',{context_id:scope.context_id,request_key:key,value:scope.value,expected_binding:scope.binding},result=>showRecord(result));
 };
 el('lookup').onclick=async()=>{
  if(!scope||!key||busy)return;
  record=null;el('record').textContent='';
  await send('show_remote_annotation_recovery',{context_id:scope.context_id,request_key:key,expected_binding:scope.binding},result=>{
   if(result?.context_id!==scope.context_id||result?.request_key!==key||result?.binding!==scope.binding){message('인증 연결이 달라졌습니다. 카드를 새로 열어 주세요.');return;}
   showRecord(result.recovery,result.recovery?.state);
  });
 };
 el('confirm').onclick=async()=>{
  if(!scope||!key||!record||busy)return;
  await send('confirm_remote_annotation_card',{context_id:scope.context_id,request_key:key,annotation_id:record.annotation_id,revision:record.revision,expected_binding:scope.binding},result=>{
   record=null;el('record').textContent='';message(result?.confirmed===true?'수신을 확정했습니다.':'확정을 확인하지 못했습니다. 기존 요청을 다시 조회하세요.');
  });
 };
 app.ontoolinput=e=>{reset('입력을 확인하는 중입니다.');incoming=e?.arguments;};
 app.ontoolcancelled=()=>reset('취소됐습니다. 새 입력으로 카드를 다시 열어 주세요.');
 function matchesInput(v, expected) {
  if (!v.quote_request) return canonical(v.value) === canonical(expected.value);
  if (typeof expected.quote !== 'string' || v.value?.kind !== 'highlight'
      || v.value.body !== (expected.note ?? '') || !Array.isArray(v.value.segments) || !v.value.segments.length
      || !v.value.segments.every(s => typeof s?.quote_snapshot === 'string')
      || !Number.isSafeInteger(v.quote_location?.pdf_page) || v.quote_location.pdf_page < 1) return false;
  const quote = v.value.segments.map(segment => segment.quote_snapshot).join('');
  return typeof quote === 'string'
    && quote.trim().replace(/\s+/gu, ' ') === expected.quote.trim().replace(/\s+/gu, ' ')
    && canonical(v.quote_request) === canonical({quote: expected.quote, pdf_page: expected.pdf_page ?? null,
      candidate_id: expected.candidate_id ?? null, note: expected.note ?? ''});
 }
 app.ontoolresult=r=>{
  const expected=incoming;incoming=null;
  const v=r?.structuredContent?.result;
  if (expected && v?.context_id === expected.context_id && typeof expected.quote === 'string'
      && r?.structuredContent?.status === 'ok' && v.outcome === 'choose_candidate') {
   reset('일치하는 위치가 여러 곳입니다. 채팅에서 페이지와 인용을 비교해 하나를 선택해 주세요.');
   el('input').textContent = v.candidates.map(c => `PDF ${c.pdf_page}페이지${c.section_title ? ` · ${c.section_title}` : ''}\n${c.excerpt}`).join('\n\n');
   return;
  }
  if (expected && v?.context_id === expected.context_id && v.outcome === 'quote_not_found') {
   reset('일치하는 원문을 찾지 못했습니다. 채팅에서 더 긴 인용이나 정확한 페이지를 알려 주세요.');return;
  }
  if(!expected||r?.isError||r?.structuredContent?.status!=='ok'||!uuid(v?.context_id)
    ||v.context_id!==expected.context_id||!matchesInput(v,expected)
    ||!['note','highlight'].includes(v.value?.kind)||typeof v.value?.body!=='string'
    ||!/^[a-f0-9]{64}$/.test(v.binding??'')){reset('입력을 확인할 수 없습니다. 새 기록을 만들지 않았습니다.');return;}
  scope=v;
  const loc = v.quote_location;
  el('input').textContent = loc
    ? `PDF ${loc.pdf_page}페이지${loc.section_title ? ` · ${loc.section_title}` : ''}\n${v.value.segments.map(segment => segment.quote_snapshot).join('')}${v.value.body ? `\n메모 · ${v.value.body}` : ''}`
    : JSON.stringify(v.value,null,2);
  if(v.existing!==null){
   if(typeof v.existing?.request_key!=='string'||v.existing.recovery!=='unique_unconfirmed'){reset('복구 결과가 불명확합니다.');return;}
   key=v.existing.request_key;el('lookup').disabled=false;showRecord(v.existing,v.existing.state);
  }else{el('new').disabled=false;message('미확정 요청이 없습니다. 기존 기록을 확인한 뒤 새 저장 여부를 선택하세요.');}
 };
 doc.defaultView?.addEventListener('pagehide',()=>reset('카드를 다시 열어 주세요.'));
 app.connect().catch(()=>reset('카드를 사용할 수 없습니다. 기존 원격 메모 도구를 이용하세요.'));
}
