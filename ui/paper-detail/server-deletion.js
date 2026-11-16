export function mountDeletion(app, document) {
  const text=document.getElementById('status');
  const title=document.getElementById('title');
  const notice=document.getElementById('notice');
  const agree=document.getElementById('agree');
  const confirm=document.getElementById('confirm');
  const refresh=document.getElementById('refresh');
  const retry=document.getElementById('retry');
  const labels={blocking:'접근 차단 · 정리 대기',cleaning:'자료 정리 중',failed:'일부 정리 미완료 가능 · 접근 차단 유지',complete:'활성 저장소 정리 완료 · 백업 완전 삭제는 미확인'};
  let current=null, generation=0, busy=false, input;
  function reset() {
    generation++;current=null;busy=false;agree.checked=false;
    title.textContent='서버 논문 삭제';notice.textContent='';text.textContent='삭제 안내를 다시 확인하세요.';
    controls();
  }
  app.ontoolinputpartial=app.ontoolcancelled=()=>{input=null;reset();};
  app.ontoolinput=event=>{input=event.arguments;reset();};
  function controls(){confirm.disabled=busy||!current?.confirmation_token||!agree.checked;refresh.disabled=busy||!current?.paper_id;retry.disabled=busy||!current?.paper_id;}
  function show(data){
    if(data?.paper_id!==current?.paper_id||!Object.hasOwn(labels,data?.status)||data.backup_erasure_confirmed!==false){throw new Error('invalid');}
    text.textContent=labels[data.status]+' · 로컬 자료는 보존됩니다.';
    current.confirmation_token=null;agree.checked=false;
  }
  async function run(name,args){
    if(busy)return;
    const version=generation;busy=true;controls();text.textContent='결과 확인 중';
    try{
      const result=await app.callServerTool({name,arguments:args});
      if(version!==generation)return;
      if(result.isError)throw new Error('unavailable');
      show(result.structuredContent);
    }catch{
      if(version===generation){text.textContent='결과 미확정 · 기존 요청을 조회하거나 같은 요청으로 재전송하세요. 새 삭제 요청은 만들지 않습니다.';current.confirmation_token=null;agree.checked=false;}
    }finally{if(version===generation){busy=false;controls();}}
  }
  app.ontoolresult=result=>{
    if(input===null||(input && input.paper_id!==result?.structuredContent?.paper_id))return;
    generation++;busy=false;agree.checked=false;current=result.structuredContent;
    if(result.isError || typeof current?.paper_id!=='string'){reset();return;}
    title.textContent=current?.title||'서버 논문 삭제 상태';
    notice.textContent=current?.notice||'서버 삭제는 로컬 삭제·동기화 중지·동의 철회와 다릅니다. 복구를 보장하지 않습니다.';
    text.textContent='삭제 범위를 확인하세요. 조회만으로 삭제하지 않습니다.';
    if(current?.status){try{show(current);}catch{text.textContent='삭제 상태 확인 필요';}}
    controls();
  };
  agree.addEventListener('change',controls);
  confirm.addEventListener('click',()=>{if(!confirm.disabled)run('confirm_server_paper_deletion',{paper_id:current.paper_id,confirmation_token:current.confirmation_token});});
  refresh.addEventListener('click',()=>{if(!refresh.disabled)run('get_server_paper_deletion',{paper_id:current.paper_id});});
  retry.addEventListener('click',()=>{if(!retry.disabled)run('retry_server_paper_deletion',{paper_id:current.paper_id});});
  controls();app.connect();
}
