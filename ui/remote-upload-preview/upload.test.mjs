import test from 'node:test';
import assert from 'node:assert/strict';
import {createPreview, renderPreview} from './upload.mjs';
const PDF = {name:'paper.pdf',type:'application/pdf',size:100};
const response = status => ({intentId:'intent-a',status,expiresAt:2000});

for (const status of ['created','uploading','verified','failed','expired']) {
  test(`대역 상태 ${status}는 접수 없이 표시`, () => {
    const model=createPreview(); const selected=model.select(PDF);
    const result=model.receive(selected.generation,response(status),1000);
    assert.equal(result.status,status);
    const html=renderPreview(result);
    assert.match(html,/paper.pdf/); assert.match(html,/100 bytes/);
    assert.equal((html.match(/disabled/g)||[]).length,3);
    assert.doesNotMatch(html,/<form|<script|href=|src=/);
    assert.doesNotMatch(html,/intent-a/);
    assert.match(html,/별도로 확인/);
  });
}
for (const change of [null,{...PDF,name:'new.pdf'}]) {
  test(`선택 변경/취소 뒤 늦은 응답 무시 ${change?.name ?? '취소'}`, () => {
    const model=createPreview(); const old=model.select(PDF);
    model.receive(old.generation,response('verified'),1000);
    const current=model.select(change);
    assert.deepEqual(model.receive(old.generation,response('verified'),1000),current);
    assert.equal(current.status,change ? 'selected':'empty');
  });
}
for (const bad of [{...PDF,size:0},{...PDF,size:52428801},{...PDF,type:'text/plain'},
  {...PDF,name:'a.txt'},{...PDF,name:'../paper.pdf'},{...PDF,size:true},{...PDF,name:'x\n.pdf'}]) {
  test(`잘못된 선택 거부 ${JSON.stringify(bad)}`, () => {
    const model=createPreview(); const selected=model.select(bad);
    assert.equal(selected.status,'invalid'); assert.equal(selected.file,null);
    assert.equal(model.receive(selected.generation,response('verified'),1000).status,'invalid');
  });
}
test('허용 최대 크기와 대문자 확장자',()=>{
  assert.equal(createPreview().select({...PDF,name:'paper.PDF',size:52428800}).status,'selected');
});
test('같은 의도 재조회 멱등과 다른 의도 혼입 거부',()=>{
  const model=createPreview();const selected=model.select(PDF);
  const one=model.receive(selected.generation,response('created'),1000);
  assert.deepEqual(model.receive(selected.generation,response('created'),1000),one);
  assert.equal(model.receive(selected.generation,{...response('verified'),intentId:'other'},1000).status,'unavailable');
});
test('만료된 진행 상태는 완료로 표시하지 않음',()=>{
  const model=createPreview();const selected=model.select(PDF);
  assert.equal(model.receive(selected.generation,response('uploading'),2000).status,'expired');
});
test('오류 원문과 미지원 상태를 표시하지 않음',()=>{
  const model=createPreview();const selected=model.select(PDF);
  const view=model.receive(selected.generation,{...response('secret-server-error'),detail:'private-token'},1000);
  assert.equal(view.status,'unavailable');assert.doesNotMatch(renderPreview(view),/secret-server|private-token/);
});
test('파일명 HTML 삽입 방어',()=>{
  const view=createPreview().select({...PDF,name:'<img src=x onerror=alert(1)>.pdf'});
  assert.equal(view.status,'selected');assert.doesNotMatch(renderPreview(view),/<img/);
  assert.match(renderPreview(view),/&lt;img/);
});
test('상태 사본 변경이 내부 선택을 바꾸지 않음',()=>{
  const model=createPreview();const selected=model.select(PDF);selected.file.name='other.pdf';
  assert.equal(model.snapshot().file.name,'paper.pdf');
});

const jobResponse = (status='queued', id='job-a') => ({intentId:'intent-a',
  job:{job_id:id,paper_id:'paper-a',server_version_id:'v1',status}});
function verified() {
  const model=createPreview(); const selected=model.select(PDF);
  model.receive(selected.generation,response('verified'),1000);
  return model;
}
for (const status of ['queued','running','succeeded','failed','cancelled']) {
  test(`검증과 처리 상태 분리 및 같은 작업 복구 ${status}`,()=>{
    const model=verified();
    assert.equal(model.snapshot().job,null);
    const first=model.receiveAdmission(model.beginAdmissionLookup(),jobResponse(status));
    const again=model.receiveAdmission(model.beginAdmissionLookup(),jobResponse(status));
    assert.deepEqual(first,again);
    assert.equal(again.status,'verified');assert.equal(again.admissionStatus,status);
    assert.equal(again.job.job_id,'job-a');
    assert.match(renderPreview(again),/기존 작업 ID: job-a/);
    assert.equal((renderPreview(again).match(/disabled/g)||[]).length,3);
  });
}
test('미검증 파일은 기존 작업 조회를 시작하지 않음',()=>{
  const model=createPreview();model.select(PDF);
  assert.equal(model.beginAdmissionLookup(),null);
  assert.equal(model.receiveAdmission(null,jobResponse()).job,null);
});
for (const change of [null,{...PDF,name:'changed.pdf'}]) {
  test(`작업 조회 중 선택 변경·취소 뒤 이전 응답 무시 ${change?.name}`,()=>{
    const model=verified();const ticket=model.beginAdmissionLookup();
    const current=model.select(change);
    assert.deepEqual(model.receiveAdmission(ticket,jobResponse()),current);
    assert.equal(current.job,null);
  });
}
test('늦은 조회와 중복 응답은 최신 조회 결과를 덮어쓰지 않음',()=>{
  const model=verified();const old=model.beginAdmissionLookup();const newest=model.beginAdmissionLookup();
  const current=model.receiveAdmission(newest,jobResponse('running'));
  assert.deepEqual(model.receiveAdmission(old,jobResponse('queued')),current);
  assert.deepEqual(model.receiveAdmission(newest,jobResponse('queued')),current);
});
test('응답 유실 뒤 같은 작업 복구·다른 작업 혼입 차단',()=>{
  const model=verified();model.receiveAdmission(model.beginAdmissionLookup(),jobResponse());
  assert.equal(model.receiveAdmission(model.beginAdmissionLookup(),null).admissionStatus,'unavailable');
  assert.equal(model.receiveAdmission(model.beginAdmissionLookup(),jobResponse('running')).job.job_id,'job-a');
  const wrong=model.receiveAdmission(model.beginAdmissionLookup(),jobResponse('running','job-b'));
  assert.equal(wrong.admissionStatus,'unavailable');assert.equal(wrong.job.job_id,'job-a');
});
test('다른 의도·비정상 응답 원문은 노출하지 않음',()=>{
  const model=verified();
  for(const value of [{...jobResponse(),intentId:'foreign'}, {intentId:'intent-a',job:{status:'secret-token'}},null]) {
    const view=model.receiveAdmission(model.beginAdmissionLookup(),value);
    assert.equal(view.admissionStatus,'unavailable');assert.equal(view.job,null);
    assert.doesNotMatch(renderPreview(view),/foreign|secret-token/);
  }
});
test('작업 ID escape와 상태 사본의 독립성',()=>{
  const model=verified();const view=model.receiveAdmission(model.beginAdmissionLookup(),jobResponse('queued','<img src=x>'));
  assert.doesNotMatch(renderPreview(view),/<img/);
  view.job.job_id='modified';assert.equal(model.snapshot().job.job_id,'<img src=x>');
});

// 실제 HTML의 이벤트 연결을 실행하여 모델 검사만으로 놓치는 취소 경로를 확인한다.
async function pageHarness() {
  const {readFileSync} = await import('node:fs');
  const {runInNewContext} = await import('node:vm');
  const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  class Element extends EventTarget {
    files = [];
    innerHTML = '';
    _value = '';
    get value() {return this._value;}
    set value(value) {this._value=value;if(value==='') this.files=[];}
  }
  const elements = Object.fromEntries(['file','cancel','fixture','apply','job-state','job-apply','preview']
    .map(id=>[id,new Element()]));
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
    .replace(/import .* from '.\/upload.mjs';/, '');
  runInNewContext(script, {createPreview,renderPreview,document:{querySelector:s=>elements[s.slice(1)]}});
  const emit = (id,type='click')=>elements[id].dispatchEvent(new Event(type));
  const choose = name=>{elements.file.files=[{...PDF,name}];emit('file','change');};
  const upload = status=>{elements.fixture.value=status;emit('apply');};
  const lookup = status=>{elements['job-state'].value=status;emit('job-apply');};
  return {elements,emit,choose,upload,lookup,html:()=>elements.preview.innerHTML};
}
for (const cancel of ['picker','button']) {
  test(`화면에서 ${cancel} 취소 후 기존 파일과 작업을 다시 표시하지 않음`,async()=>{
    const page=await pageHarness();
    page.choose('old.pdf');page.upload('verified');page.lookup('running');
    assert.match(page.html(),/기존 작업 ID:/);
    page.emit(cancel==='picker'?'file':'cancel',cancel==='picker'?'cancel':'click');
    assert.equal(page.elements.file.files.length,0);
    assert.match(page.html(),/PDF를 선택하세요/);
    assert.doesNotMatch(page.html(),/old.pdf|기존 작업 ID:/);
    page.upload('verified');page.lookup('succeeded');
    assert.doesNotMatch(page.html(),/old.pdf|기존 작업 ID:/);
    page.choose('old.pdf');
    assert.match(page.html(),/아직 서버로 전송하지/);
    assert.doesNotMatch(page.html(),/기존 작업 ID:/);
  });
}
test('화면에서 파일 교체 후 검증 전에는 이전 작업을 표시하지 않음',async()=>{
  const page=await pageHarness();page.choose('old.pdf');page.upload('verified');page.lookup('running');
  page.choose('new.pdf');page.lookup('succeeded');
  assert.match(page.html(),/new.pdf/);
  assert.doesNotMatch(page.html(),/old.pdf|기존 작업 ID:/);
});
test('화면에서 응답 유실 후 재조회는 동일 작업 ID를 유지',async()=>{
  const page=await pageHarness();page.choose('paper.pdf');page.upload('verified');page.lookup('queued');
  const id=page.html().match(/기존 작업 ID: ([^<]+)/)[1];
  page.lookup('응답 유실');assert.match(page.html(),/접수 여부를 확인하지 못했습니다/);
  page.lookup('running');assert.match(page.html(),/기존 작업을 처리 중/);
  assert.equal(page.html().match(/기존 작업 ID: ([^<]+)/)[1],id);
});
