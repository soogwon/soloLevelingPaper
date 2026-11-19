const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v);
export function mountRemoteQuiz(app,doc,crypto=doc.defaultView.crypto){
 const el=id=>doc.getElementById(id);
 let generation=0,input=null,scope=null,key=null,busy=false,attemptId=null;
 function reset(message){++generation;attemptId=null;el('details').hidden=true;el('details').disabled=false;el('details-output').textContent='';input=null;scope=null;key=null;busy=false;el('questions').replaceChildren();el('review').textContent='';el('confirm').checked=false;for(const id of ['confirm','submit','lookup'])el(id).disabled=true;el('status').textContent=message;}
 function result(v){
  if(!scope||v?.context_id!==scope.context_id||v.quiz_id!==scope.quiz_id||v.binding!==scope.binding||v.request_key!==key)throw Error();
  const a=v.attempt;
  if(a===null){el('status').textContent='같은 키의 저장 결과가 아직 없습니다. 자동 재제출하지 않습니다.';return;}
  if(!uuid(a?.attempt_id)||a.context_id!==scope.context_id||a.quiz_id!==scope.quiz_id||a.request_key!==key||!Array.isArray(a.results)||!Number.isInteger(a.correct_count)||!Number.isInteger(a.total_count))throw Error();
  el('review').textContent=`정답 ${a.correct_count}/${a.total_count}\n`+a.results.filter(r=>!r.is_correct).map(r=>`${r.position+1}. ${r.prompt}\n정답: ${r.correct_answer}\n채점 기준: ${r.grading_criteria}\n고정 근거: ${r.evidence_ids.join(', ')}`).join('\n\n');
  attemptId=a.attempt_id;el('details').hidden=false;el('details-output').textContent='';
  el('status').textContent='저장된 제출 결과입니다.';
 }
 async function send(name,args){
  if(!scope||busy)return;const gen=generation;busy=true;el('submit').disabled=el('lookup').disabled=el('confirm').disabled=true;
  try{const response=await app.callServerTool({name,arguments:args});if(gen!==generation)return;if(response?.isError||response?.structuredContent?.status!=='ok')throw Error();result(response.structuredContent.result);}
  catch{if(gen===generation){el('review').textContent='';el('status').textContent='제출 결과를 확인하지 못했습니다. 같은 요청 결과 조회를 사용하세요. 새 키로 제출하지 마세요.';}}
  finally{if(gen===generation){busy=false;el('lookup').disabled=!key;}}
 }
 el('confirm').onchange=()=>{el('submit').disabled=!scope||busy||!!key||!el('confirm').checked;};
 el('submit').onclick=async()=>{
  if(!scope||key||busy||!el('confirm').checked||scope.consent?.storage!=='current')return;
  const answers={};
  for(const q of scope.quiz.questions){const field=el(`answer-${q.position}`);if(field.value===''){el('status').textContent='모든 문항에 답안을 입력하세요.';return;}answers[q.position]=q.question_type==='multiple_choice'?Number(field.value):field.value;}
  key=crypto.randomUUID();if(!uuid(key)){reset('요청 키를 준비하지 못했습니다.');return;}
  el('confirm').checked=false;for(const field of el('questions').querySelectorAll('input,select'))field.disabled=true;
  await send('submit_remote_quiz_card',{context_id:scope.context_id,quiz_id:scope.quiz_id,expected_binding:scope.binding,request_key:key,answers,storage_consent_id:scope.consent.consent_id});
 };
 el('lookup').onclick=()=>key&&scope&&send('get_remote_quiz_submission',{context_id:scope.context_id,quiz_id:scope.quiz_id,expected_binding:scope.binding,request_key:key});
 el('details').onclick=async()=>{
  if(!scope||!attemptId||el('details').disabled)return;
  const current=generation, id=attemptId;
  el('details').disabled=true;
  try{
   const reply=await app.callServerTool({name:'get_remote_quiz_card_review',arguments:{
    context_id:scope.context_id,quiz_id:scope.quiz_id,attempt_id:id,expected_binding:scope.binding}});
   if(current!==generation||id!==attemptId)return;
   const v=reply?.structuredContent?.result;
   if(reply?.isError||reply?.structuredContent?.status!=='ok'||v?.context_id!==scope.context_id||
      v.quiz_id!==scope.quiz_id||v.attempt_id!==id||v.binding!==scope.binding||
      !Array.isArray(v.scores)||!Array.isArray(v.evidence))throw Error();
   const score=v.scores.filter(s=>s.attempts>0).map(s=>
    `${s.step}: 최근 ${Math.round(s.latest_score*100)}%, 최고 ${Math.round(s.best_score*100)}% (${s.attempts}회)`);
   const excerpts=v.evidence.map(e=>`PDF ${e.pdf_page}페이지 · ${e.evidence_id}\n${e.quote_original}`);
   const rest=v.evidence_count-excerpts.length;
   el('details-output').textContent=[...score,...excerpts,...(rest>0?[`표시하지 못한 근거 ${rest}건은 위 근거 ID로 확인하세요.`]:[])].join('\n\n');
  }catch{
   if(current===generation&&id===attemptId)el('details-output').textContent='점수 또는 근거를 불러오지 못했습니다.';
  }finally{
   if(current===generation)el('details').disabled=false;
  }
 };
 app.ontoolinputpartial=()=>reset('퀴즈를 확인하는 중입니다.');
 app.ontoolinput=e=>{reset('퀴즈를 확인하는 중입니다.');input=e?.arguments;};
 app.ontoolcancelled=()=>reset('취소됐습니다. 카드를 다시 열어 주세요.');
 app.ontoolresult=r=>{
  const expected=input;input=null;const v=r?.structuredContent?.result;
  if(!expected||r?.isError||r?.structuredContent?.status!=='ok'||!uuid(v?.context_id)||v.context_id!==expected.context_id||!uuid(v.quiz_id)||!/^[a-f0-9]{64}$/.test(v.binding??'')){reset('퀴즈를 확인할 수 없습니다.');return;}
  if(expected.attempt_id){
   if(v.attempt?.attempt_id!==expected.attempt_id||!uuid(v.request_key)){reset('다른 제출 결과입니다.');return;}
   scope=v;key=v.request_key;try{result(v);el('lookup').disabled=false;}catch{reset('제출 결과가 불명확합니다.');}return;
  }
  if(v.quiz_id!==expected.quiz_id||v.quiz?.context_id!==v.context_id||v.quiz?.quiz_id!==v.quiz_id||!Array.isArray(v.quiz?.questions)||!v.quiz.questions.length){reset('다른 퀴즈입니다.');return;}
  scope=v;
  for(const q of v.quiz.questions){
   const label=doc.createElement('label');label.textContent=`${q.position+1}. ${q.prompt}`;
   const field=doc.createElement(q.question_type==='multiple_choice'?'select':'input');field.id=`answer-${q.position}`;
   if(q.question_type==='multiple_choice'){const blank=doc.createElement('option');blank.value='';blank.textContent='선택하세요';field.append(blank);q.choices.forEach((choice,i)=>{const option=doc.createElement('option');option.value=String(i);option.textContent=choice;field.append(option);});}else field.maxLength=5000;
   label.append(field);el('questions').append(label,doc.createElement('br'));
  }
  el('confirm').disabled=v.consent?.storage!=='current';
  el('status').textContent=v.consent?.storage==='current'?'답안을 확인하고 명시적으로 제출하세요. 단답형은 정규화된 정답과 정확히 일치해야 합니다.':'인증 웹의 퀴즈 답안 저장 동의를 확인한 뒤 카드를 다시 열어 주세요. AI 전송 동의와 별개입니다.';
 };
 doc.defaultView?.addEventListener('pagehide',()=>reset('카드를 다시 열어 주세요.'));
 app.connect().catch(()=>reset('카드를 열지 못했습니다. 기존 퀴즈 조회·제출 도구를 사용하세요.'));
}
