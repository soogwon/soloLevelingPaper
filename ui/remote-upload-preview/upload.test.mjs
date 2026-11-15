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
