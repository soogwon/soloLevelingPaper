const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
const FAILURES={
 not_found:'학습을 시작한 논문에서만 메모를 저장할 수 있습니다. 먼저 이 논문 학습을 시작해 주세요.',
 not_authorized:'연결 인증이 만료됐습니다. 커넥터를 다시 연결한 뒤 요청해 주세요.',
 consent_required:'계정의 서버 저장 동의가 철회돼 저장하지 못했습니다. 웹의 계정 설정에서 확인하세요.',
};
const canonical = v => JSON.stringify(v, function(k,x) {return x && typeof x==='object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])) : x;});
export function mountRemoteCompose(app,doc,crypto=doc.defaultView.crypto) {
 const el=id=>doc.getElementById(id);
 let generation=0, incoming=null, scope=null, key=null, record=null, busy=false;
 const BUTTONS=['save','lookup','confirm'];
 // 쓸 수 없는 버튼은 숨겨 실패 안내와 저장 안내가 함께 보이지 않게 한다.
 function show(id,on){el(id).disabled=!on;el(id).hidden=!on;}
 function reset(message) {
  ++generation; incoming=null;scope=null;key=null;record=null;busy=false;
  for(const id of BUTTONS)show(id,false);
  el('input').textContent='';el('record').textContent='';el('status').textContent=message;
 }
 function message(text) {el('status').textContent=text;}
 function showRecord(result, state='committed', fresh=false) {
  record=null;el('record').textContent='';show('confirm',false);
  const a=result?.annotation;
  if (result?.request_key!==key || !uuid(a?.annotation_id) || a.context_id!==scope.context_id
      || a.kind!==scope.value.kind || !Number.isSafeInteger(a.revision) || a.revision<1 || !['note','highlight'].includes(a.kind)
      || typeof a.body!=='string' || !Array.isArray(a.segments)) {message('저장 결과가 불명확합니다. 같은 요청으로 다시 조회하세요.');return;}
  if(state==='deleted'){message('삭제된 기록입니다. 새 키로 재생성하지 않습니다.');return;}
  if(state!=='committed'||!a.segments.every(s=>s&&['original','translation'].includes(s.target)&&typeof s.quote_snapshot==='string')){message('기록을 확인할 수 없습니다.');return;}
  record=a;el('record').textContent=`${a.kind} · ${a.annotation_id} · revision ${a.revision}\n${a.body}\n`+a.segments.map(s=>`${s.target==='translation'?'번역':'원문'}: ${s.quote_snapshot}`).join('\n');
  show('confirm',true);message(state==='committed'&&!fresh?'이전에 저장된 결과입니다. 내용을 확인한 뒤 수신을 확정하세요.':'저장했습니다. 내용을 확인한 뒤 수신을 확정하세요.');
 }
 async function send(name,args,accept) {
  if(!scope||busy)return;
  const current=generation;busy=true;
  for(const id of BUTTONS)el(id).disabled=true;
  try {const r=await app.callServerTool({name,arguments:args});if(current!==generation)return;
   if(r?.isError||r?.structuredContent?.status!=='ok'){record=null;el('record').textContent='';message(r?.structuredContent?.status==='consent_required'?'계정의 서버 저장 동의가 철회돼 저장하지 못했습니다. 웹의 계정 설정에서 확인하세요.':'저장·확인을 완료하지 못했습니다. 기존 요청을 다시 조회하세요.');}
   else accept(r.structuredContent.result);
  }catch{if(current===generation){record=null;el('record').textContent='';message('응답이 유실됐을 수 있습니다. 새 키로 저장하지 말고 기존 요청을 조회하세요.');}}
  finally{if(current===generation){busy=false;show('save',false);show('lookup',!!key);el('confirm').disabled=!record;}}
 }
 el('save').onclick=async()=>{
  if(!scope||key||busy)return;
  try{key=crypto.randomUUID();if(!uuid(key))throw Error();}catch{reset('요청 키를 준비할 수 없습니다. 저장하지 않았습니다.');return;}
  await send('save_remote_annotation_card',{context_id:scope.context_id,request_key:key,value:scope.value,expected_binding:scope.binding},result=>showRecord(result,'committed',true));
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
   record=null;el('record').textContent='';show('confirm',false);message(result?.confirmed===true?'수신을 확정했습니다.':'확정을 확인하지 못했습니다. 기존 요청을 다시 조회하세요.');
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
  const sameContext = !expected || v?.context_id === expected.context_id;
  if (sameContext && (!expected || typeof expected.quote === 'string') && Array.isArray(v?.candidates)
      && r?.structuredContent?.status === 'ok' && v.outcome === 'choose_candidate') {
   reset('일치하는 위치가 여러 곳입니다. 채팅에서 페이지와 인용을 비교해 하나를 선택해 주세요.');
   el('input').textContent = v.candidates.map(c => `PDF ${c.pdf_page}페이지${c.section_title ? ` · ${c.section_title}` : ''}\n${c.excerpt}`).join('\n\n');
   return;
  }
  if (sameContext && r?.structuredContent?.status === 'ok' && v?.outcome === 'quote_not_found') {
   reset('일치하는 원문을 찾지 못했습니다. 채팅에서 더 긴 인용이나 정확한 페이지를 알려 주세요.');return;
  }
  const status=r?.structuredContent?.status;
  if(r?.isError||status!=='ok'){reset(FAILURES[status]??'메모를 준비하지 못했습니다. 저장하지 않았습니다. 채팅에서 다시 요청해 주세요.');return;}
  // 호스트가 입력 알림을 보내지 않았으면 서버가 돌려준 준비 결과를 그대로 보여 주고 사용자가 저장을 누를 때만 저장한다.
  if(!uuid(v?.context_id)||(expected&&(v.context_id!==expected.context_id||!matchesInput(v,expected)))
    ||!['note','highlight'].includes(v.value?.kind)||typeof v.value?.body!=='string'
    ||!/^[a-f0-9]{64}$/.test(v.binding??'')){reset('요청한 메모와 준비 결과가 일치하지 않아 저장하지 않았습니다. 채팅에서 다시 요청해 주세요.');return;}
  scope=v;
  const loc = v.quote_location;
  el('input').textContent = loc
    ? `PDF ${loc.pdf_page}페이지${loc.section_title ? ` · ${loc.section_title}` : ''}\n${v.value.segments.map(segment => segment.quote_snapshot).join('')}${v.value.body ? `\n메모 · ${v.value.body}` : ''}`
    : JSON.stringify(v.value,null,2);
  if(v.existing!==null){
   if(typeof v.existing?.request_key!=='string'||v.existing.recovery!=='unique_unconfirmed'){reset('복구 결과가 불명확합니다.');return;}
   key=v.existing.request_key;show('lookup',true);showRecord(v.existing,v.existing.state);
  }else{show('save',true);message('내용을 확인하고 저장을 누르세요.');}
 };
 doc.defaultView?.addEventListener('pagehide',()=>reset('카드를 다시 열어 주세요.'));
 app.connect().catch(()=>reset('카드를 사용할 수 없습니다. 기존 원격 메모 도구를 이용하세요.'));
}
