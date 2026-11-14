import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { webcrypto } from 'node:crypto';
import { mountDetailApp } from './detail.js';
import { prepareAnnotationRequest } from './annotation-request.js';
const html = await readFile(new URL('./template.html', import.meta.url),'utf8');
const evidence = (id='e1', changes={}) => ({evidence_id:id,chunk_id:`c-${id}`,version_id:'v',parse_revision_id:'p',translation_revision_id:'t',pdf_page:6,printed_page_label:null,quote_original:'A is faster when',quote_ko:'A가 빠른 조건',...changes});
function setup(connect=async()=>{},timeout=1000){
 const doc=new JSDOM(html, {url:'https://card.test'}).window.document;
 Object.defineProperty(doc.defaultView, 'crypto', {value:webcrypto});
 const app={connect,callServerTool:()=>{throw Error('자동 호출 금지');}};
 mountDetailApp(app,doc,timeout); return {app,doc,el:id=>doc.getElementById(id)};
}
const payload=(items)=>({structuredContent:{evidence:items}});
test('앞뒤 근거는 자기 페이지와 출처를 유지하며 버튼으로 이동한다',()=>{
 const {app,el,doc}=setup();
 app.ontoolresult({structuredContent:{status:'ok',verification_level:'model_checked',answer_ko:'n < d 조건',claims:[{text:'조건',evidence_ids:['e1','e2']}],citations:[evidence(),evidence('e2',{pdf_page:7,quote_original:'n < d.',quote_ko:null})]}});
 assert.equal(el('claims').querySelectorAll('button').length,2);
 el('claims').querySelectorAll('button')[1].click();
 assert.match(el('location').textContent,/7페이지/); assert.equal(el('original').textContent,'n < d.');
 assert.match(el('translation').textContent,/없습니다/); assert.match(el('provenance').textContent,/e2/);
 assert.equal(doc.activeElement,el('detail')); assert.equal(el('evidence-list').lastChild.getAttribute('aria-pressed'),'true');
});
test('본문·파일명은 HTML로 실행하지 않는다',()=>{
 const {app,el,doc}=setup();
 app.ontoolresult(payload([evidence('e1',{quote_original:'<img src=x onerror=alert(1)>',file_display_name:'<script>bad</script>'})]));
 assert.equal(doc.querySelectorAll('img,script').length,0);assert.match(el('original').textContent,/<img/);
 assert.match(el('file').textContent,/<script>/);
});
test('새 입력·오류·취소는 이전 본문을 제거하고 오류 원문은 숨긴다',()=>{
 const {app,el}=setup();app.ontoolresult(payload([evidence()]));app.ontoolinput({arguments:{}});
 assert.equal(el('original').textContent,'');assert.equal(el('content').hidden,true);
 app.ontoolresult({isError:true,content:[{text:'secret'}]});assert.doesNotMatch(el('message').textContent,/secret/);
 app.ontoolcancelled();assert.match(el('message').textContent,/취소/);
});
test('서로 다른 리비전·중복 ID·없는 참조·잘못된 페이지를 거부한다',()=>{
 const {app,el}=setup();
 for(const items of [[evidence(),evidence('e2',{translation_revision_id:'other'})],[evidence(),evidence()],[evidence('e1',{pdf_page:0})]]){
 app.ontoolresult(payload(items));assert.equal(el('content').hidden,true);assert.match(el('message').textContent,/출처 연결/);
 }
 app.ontoolresult({structuredContent:{status:'ok',verification_level:'model_checked',answer_ko:'답',claims:[{text:'주장',evidence_ids:['missing']}],citations:[evidence()]}});
 assert.equal(el('content').hidden,true);
});
test('빈 근거도 반환된 답변과 제한을 보존한다',()=>{
 const {app,el}=setup();app.ontoolresult({structuredContent:{status:'insufficient_evidence',verification_level:'structural_only',answer_ko:'검증하지 못했습니다.',claims:[],citations:[]}});
 assert.equal(el('answer').textContent,'검증하지 못했습니다.');assert.equal(el('detail').hidden,true);
});
test('연결 실패와 지연은 안전한 안내를 표시한다',async()=>{
 const a=setup(async()=>{throw Error('private');});await new Promise(r=>setTimeout(r,0));assert.match(a.el('message').textContent,/연결에 실패/);
 const b=setup(async()=>{},5);await new Promise(r=>setTimeout(r,15));assert.match(b.el('message').textContent,/지연/);
});

const detailPayload = (changes={})=>({structuredContent:{context_id:'ctx',paper_id:'paper',title:null,file_display_name:'paper.pdf',version_id:'v',parse_revision_id:'p',translation_revision_id:'t',scope:'paper',section_ids:['s1'],summary:{summary_id:'sum1',items:[{kind:'method',text:'저장된 방법',evidence_ids:['e1']}],total_items:2,next_offset:1,verification_level:'structural_only'},evidence:[evidence('e1',{section_id:'s1'})],...changes}});
test('저장 요약은 종류·근거·섹션과 다음 페이지 조회 정보를 표시한다',()=>{
 const {app,el}=setup();app.ontoolresult(detailPayload());
 assert.equal(el('content').hidden,false);
 assert.match(el('claims').textContent,/방법: 저장된 방법/);
 el('claims').querySelector('button').click();
 assert.match(el('location').textContent,/섹션 ID s1/);
 assert.match(el('pagination').textContent,/"summary_id":"sum1"/);
 assert.match(el('pagination').textContent,/"offset":1/);
 assert.match(el('availability').textContent,/키워드/);
 assert.match(el('verification').textContent,/의미 일치 검증은 아닙니다/);
});
test('요약 없음은 생성하지 않고 안내하며 잘못된 요약 연결은 거부한다',()=>{
 const {app,el}=setup();app.ontoolresult(detailPayload({summary:null,evidence:[]}));
 assert.match(el('answer').textContent,/저장된 요약이 없습니다/);
 assert.equal(el('detail').hidden,true);
 for(const changes of [{version_id:'other'},{evidence:[null]},{summary:{items:[],verification_level:'model_checked'}},{section_ids:'s1'}]){
 app.ontoolresult(detailPayload(changes));assert.equal(el('content').hidden,true);
 }
});

test('요약 없음에서 검증 안내와 빈 항목 영역을 숨기고 다음 결과에서 복원한다',()=>{
 const {app,el}=setup();
 app.ontoolresult(detailPayload());
 app.ontoolresult(detailPayload({summary:null,evidence:[]}));
 assert.equal(el('claims-section').hidden,true);
 assert.equal(el('verification').hidden,true);
 assert.equal(el('verification').textContent,'');
 assert.equal(el('claims').children.length,0);
 assert.match(el('message').textContent,/저장된 요약이 없어/);
 app.ontoolresult(detailPayload());
 assert.equal(el('claims-section').hidden,false);
 assert.equal(el('verification').hidden,false);
 assert.equal(el('claims-heading').textContent,'요약 항목별 근거로 이동');
 el('claims').querySelector('button').click();
 assert.equal(el('original').textContent,'A is faster when');
 assert.equal(el('translation').textContent,'A가 빠른 조건');
 assert.match(el('location').textContent,/6페이지/);
});
test('빈 답변의 실제 검증 수준은 유지하고 빈 주장 영역만 숨긴다',()=>{
 const {app,el}=setup();
 app.ontoolresult({structuredContent:{status:'insufficient_evidence',verification_level:'structural_only',answer_ko:'검증하지 못했습니다.',claims:[],citations:[]}});
 assert.equal(el('claims-section').hidden,true);
 assert.equal(el('verification').hidden,false);
 assert.match(el('verification').textContent,/structural_only/);
});

test('저장된 목차만 표시하고 다른 파싱 리비전의 제목은 거부한다',()=>{
 const {app,el}=setup();
 const row={section_id:'s1',parse_revision_id:'p',title:'Methods',ordinal:1,level:1,parent_section_id:null,start_pdf_page:5,end_pdf_page:7};
 app.ontoolresult(detailPayload({section_catalog:[row]}));
 assert.match(el('toc-items').textContent,/Methods · PDF 5–7페이지/);
 assert.match(el('location').textContent,/Methods/);
 app.ontoolresult(detailPayload({section_catalog:[{...row,parse_revision_id:'other'}]}));
 assert.equal(el('content').hidden,true);
 assert.equal(el('toc-items').children.length,0);
 app.ontoolresult(detailPayload({section_catalog:[]}));
 assert.match(el('toc-status').textContent,/저장된 목차가 없습니다/);
});

test('목차 제목과 메모 저장을 함께 유지하고 출처 불일치와 데이터 부재를 구분한다', async () => {
  const {app, el} = setup();
  const row = {section_id:'s1',parse_revision_id:'p',title:'Methods',ordinal:1,level:1,
    parent_section_id:null,start_pdf_page:5,end_pdf_page:7};
  const first = evidence('e1', {section_id:'s1',annotation_start:0,annotations:[]});
  let request;
  app.callServerTool = async call => {
    request = call;
    return {structuredContent:{outcome:'saved',annotation:{annotation_id:'a',context_id:'ctx',kind:'note',body:'내 메모'}}};
  };
  app.ontoolresult(detailPayload({section_catalog:[row],evidence:[first],keywords:[{text:'Methods'}]}));
  assert.match(el('toc-items').textContent,/Methods/);
  assert.match(el('location').textContent,/Methods/);
  assert.match(el('availability').textContent,/키워드의 일부 출처를 확인할 수 없습니다/);
  assert.match(el('availability').textContent,/저장된 중요 문장 없음/);
  el('note-body').value = '내 메모';
  await el('save-note').onclick();
  assert.equal(request.name,'create_annotation');
  assert.equal(request.arguments.request.context_id,'ctx');
  assert.equal(request.arguments.request.segments[0].chunk_id,first.chunk_id);
  assert.match(el('records').textContent,/내 메모/);
  assert.match(el('location').textContent,/Methods/);
  assert.match(el('toc-items').textContent,/Methods/);
});

test('저장 중 근거를 전환해도 요청 위치와 표시 목록은 섞이지 않는다', async () => {
  const {app, el} = setup();
  const first = evidence('e1', {annotation_start: 3, annotations: [], quote_original: '😀가'});
  const second = evidence('e2', {annotation_start: 0, annotations: []});
  let finish;
  let request;
  app.callServerTool = call => {
    request = call.arguments.request;
    return new Promise(resolve => { finish = resolve; });
  };
  app.ontoolresult(detailPayload({evidence: [first, second]}));
  el('note-body').value = '첫 근거의 메모';
  el('save-note').click();
  while (!request) await new Promise(resolve => setImmediate(resolve));
  assert.equal(request.context_id, 'ctx');
  assert.deepEqual(request.segments, [{chunk_id:'c-e1', start_offset:3, end_offset:5, quote_snapshot:'😀가'}]);
  el('evidence-list').lastChild.click();
  finish({structuredContent: {outcome:'saved', annotation: {...request, annotation_id:'a1'}}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(el('records').children.length, 0);
  el('evidence-list').firstChild.click();
  assert.match(el('records').textContent, /첫 근거의 메모/);
});

test('더 보기는 선택한 근거의 커서로만 조회하고 실패 후 다시 시도할 수 있다', async () => {
  const {app, el} = setup();
  const cursor = 'opaque-next-page';
  const first = evidence('e1', {annotation_start:0, annotation_count:2, annotation_cursor:cursor,
    annotations:[{annotation_id:'a2', kind:'note', body:'최신 기록'}]});
  const calls = [];
  app.callServerTool = async call => {
    calls.push(call);
    if (calls.length === 1) throw Error();
    return {structuredContent:{context_id:'ctx', evidence_id:'e1', annotation_start:0,
      annotation_count:2, annotation_cursor:null, annotations:[{annotation_id:'a1', kind:'note', body:'이전 기록'}]}};
  };
  app.ontoolresult(detailPayload({evidence:[first]}));
  assert.equal(calls.length, 0);
  await el('more-records').onclick();
  assert.equal(el('more-records').disabled, false);
  assert.match(el('record-status').textContent, /다시 시도/);
  await el('more-records').onclick();
  assert.deepEqual(calls[1], {name:'list_evidence_annotations', arguments:{context_id:'ctx', evidence_id:'e1', cursor}});
  assert.match(el('records').textContent, /최신 기록.*이전 기록/);
  assert.equal(el('more-records').hidden, true);
});

test('저장 결과 미확정 뒤에는 근거 재선택으로 재시도할 수 없고 상세 재조회로 복구한다', async () => {
  const {app, el} = setup();
  const first = evidence('e1', {annotation_start:0, annotations:[]});
  const second = evidence('e2', {annotation_start:0, annotations:[]});
  let calls = 0;
  app.callServerTool = async () => { calls++; return {isError:true}; };
  app.ontoolresult(detailPayload({evidence:[first,second]}));
  await el('save-highlight').onclick();
  el('evidence-list').lastChild.click();
  assert.equal(el('editor').disabled,false);
  el('evidence-list').firstChild.click();
  assert.equal(el('editor').disabled,true);
  await el('save-highlight').onclick();
  assert.equal(calls,1);
  app.ontoolresult(detailPayload({evidence:[{...first,annotations:[{kind:'highlight',body:''}]}]}));
  assert.equal(el('editor').disabled,false);
  assert.match(el('records').textContent,/하이라이트/);
});

test('응답 유실 후 카드 재시작에서도 같은 요청 키를 사용하고 목록을 중복 표시하지 않는다', async () => {
  const first = evidence('e1', {annotation_start:0, annotations:[]});
  const a = setup();
  let original;
  a.app.callServerTool = async call => { original = call.arguments.request; throw Error(); };
  a.app.ontoolresult(detailPayload({evidence:[first]}));
  a.el('note-body').value = '미확정 메모';
  await a.el('save-note').onclick();
  assert.ok(original.request_key);
  const storage = a.doc.defaultView.localStorage;
  assert.equal(storage.length, 1);
  assert.doesNotMatch(storage.key(0) + storage.getItem(storage.key(0)), /미확정 메모|c-e1/);
  const b = setup();
  b.doc.defaultView.localStorage.setItem(storage.key(0), storage.getItem(storage.key(0)));
  const record = {...original, annotation_id:'a1'};
  b.app.ontoolresult(detailPayload({evidence:[{...first, annotations:[record], annotation_count:1}]}));
  b.app.callServerTool = async call => {
    assert.deepEqual(call.arguments.request, original);
    return {structuredContent:{outcome:'saved',annotation:record}};
  };
  b.el('note-body').value = '미확정 메모';
  await b.el('save-note').onclick();
  assert.equal(b.el('records').children.length, 1);
  assert.equal(b.doc.defaultView.localStorage.length, 0);
});

test('요청 키 보관 실패 시 생성 도구를 호출하지 않는다', async () => {
  const {app, doc, el} = setup();
  Object.defineProperty(doc.defaultView, 'localStorage', {get(){throw Error('blocked');}});
  let calls = 0;
  app.callServerTool = async () => { calls++; };
  app.ontoolresult(detailPayload({evidence:[evidence('e1',{annotation_start:0,annotations:[]})]}));
  await el('save-highlight').onclick();
  assert.equal(calls, 0);
  assert.equal(el('editor').disabled, false);
  assert.match(el('record-status').textContent, /전송하지 않았습니다/);
});

test('동일 입력 키는 보존하고 성공 확인 뒤 의도적인 새 저장에는 새 키를 만든다', async () => {
  const {doc} = setup();
  const storage = doc.defaultView.localStorage;
  const request = {context_id:'ctx',kind:'note',body:'메모',segments:[]};
  const first = await prepareAnnotationRequest(request, storage, webcrypto);
  const second = await prepareAnnotationRequest(request, storage, webcrypto);
  assert.equal(first.request.request_key, second.request.request_key);
  const other = await prepareAnnotationRequest({...request,context_id:'other'}, storage, webcrypto);
  assert.notEqual(first.request.request_key, other.request.request_key);
  first.confirm();
  const fresh = await prepareAnnotationRequest(request, storage, webcrypto);
  assert.notEqual(first.request.request_key, fresh.request.request_key);
});

test('다음 페이지의 기존 기록 재사용은 건수를 늘리지 않고 더 보기에서도 중복하지 않는다', async () => {
  const {app, el} = setup();
  const old = {annotation_id:'old',context_id:'ctx',kind:'note',body:'수정된 메모'};
  app.ontoolresult(detailPayload({evidence:[evidence('e1',{annotation_start:0,
    annotations:[{annotation_id:'new',kind:'note',body:'최근 메모'}],annotation_count:2,annotation_cursor:'next'})]}));
  app.callServerTool = async call => call.name === 'create_annotation'
    ? {structuredContent:{outcome:'saved',reused:true,annotation:old}}
    : {structuredContent:{context_id:'ctx',evidence_id:'e1',annotation_start:0,
        annotations:[old],annotation_count:2,annotation_cursor:null}};
  el('note-body').value = '최초 메모';
  await el('save-note').onclick();
  assert.match(el('record-status').textContent, /전체 2건/);
  await el('more-records').onclick();
  assert.equal(el('records').children.length, 2);
  assert.match(el('records').textContent, /수정된 메모/);
});

test('저장된 추출·생성 항목을 구분하고 잘못된 번역 출처의 본문은 숨긴다',()=>{
 const {app,el}=setup();
 const source={insight_id:'i1',text:'🧪',content_kind:'extracted',producer:'extractor',chunk_id:'c',parse_revision_id:'p',translation_revision_id:null,start_offset:1,end_offset:2,quote_original:'🧪',pdf_page:6,section_id:null};
 app.ontoolresult(detailPayload({keywords:[source],important_sentences:[
   {...source,insight_id:'i2',text:'해석',content_kind:'generated',producer:'model',translation_revision_id:'t'},
   {...source,translation_revision_id:'other',quote_original:'X',text:'X'}]}));
 assert.match(el('insights').textContent,/원문 추출 · 🧪/);
 assert.match(el('insights').textContent,/AI 생성 · 해석/);
 assert.match(el('insights').textContent,/원문 구간 \[1, 2\)/);
 assert.doesNotMatch(el('insights').textContent,/X/);
 assert.match(el('availability').textContent,/일부 출처/);
 app.ontoolinput({});
 assert.equal(el('insights').children.length,0);
});

test('근거 없는 맥락 메모를 표시하고 같은 맥락의 다음 페이지를 이어 읽는다', async () => {
  const {app, el} = setup();
  const note = {annotation_id:'a', context_id:'ctx', kind:'note', body:'맥락 메모', segments:[]};
  app.ontoolresult(detailPayload({summary:null, evidence:[], annotation_page:{
    context_id:'ctx', items:[note], next_cursor:'next',
  }}));
  const panel = el('context-records');
  assert.equal(panel.hidden, false);
  assert.match(panel.textContent, /학습 맥락에만 저장 · 맥락 메모/);
  app.callServerTool = async request => {
    assert.deepEqual(request, {name:'list_annotations', arguments:{request:{context_id:'ctx', cursor:'next'}}});
    return {structuredContent:{items:[{...note, annotation_id:'b', body:'', kind:'highlight', segments:[{quote_snapshot:'인용'}]}], next_cursor:null}};
  };
  await panel.querySelector('button').onclick();
  assert.equal(panel.querySelectorAll('li').length, 2);
  assert.match(panel.textContent, /원문 구간 연결 · 인용/);
  assert.equal(panel.querySelector('button').hidden, true);
  app.ontoolcancelled();
  assert.equal(panel.hidden, true);
  assert.equal(panel.querySelectorAll('li').length, 0);
});

for (const kind of ['note', 'highlight']) {
  test(`${kind} 수정 충돌 후 명시적으로 재조회하고 새 리비전으로 수정·삭제한다`, async () => {
    const {app, el} = setup();
    let record = {annotation_id:'a', context_id:'ctx', kind, revision:1, body:'처음', segments:[]};
    app.ontoolresult(detailPayload({summary:null, evidence:[], annotation_page:{
      context_id:'ctx', items:[record], next_cursor:null,
    }}));
    const panel = el('context-records');
    const press = label => [...panel.querySelectorAll('button')].find(b => b.textContent === label).onclick();
    const requests = [];
    app.callServerTool = async ({name, arguments:{request}}) => {
      requests.push(name);
      assert.equal(request.context_id,'ctx');
      assert.equal(request.annotation_id,'a');
      if (name === 'get_annotation') return {structuredContent:{annotation:record}};
      if (request.expected_revision !== record.revision) return {isError:true, content:[{type:'text',text:'CONFLICT: 충돌'}]};
      if (name === 'delete_annotation') return {structuredContent:{outcome:'deleted',annotation:null}};
      record = {...record, body:request.body, revision:record.revision+1};
      return {structuredContent:{outcome:'saved',annotation:record}};
    };
    press('편집');
    record = {...record, body:'다른 변경', revision:2};
    await press('수정 저장');
    assert.match(panel.textContent,/다른 변경이 먼저/);
    assert.deepEqual(requests,['update_annotation']);
    await press('최신 기록 다시 조회');
    press('편집');
    assert.equal(panel.querySelector('textarea').value,'다른 변경');
    panel.querySelector('textarea').value='내 변경';
    await press('수정 저장');
    assert.equal(record.revision,3);
    assert.match(panel.textContent,/내 변경/);
    press('편집'); press('삭제'); press('취소');
    assert.equal(requests.includes('delete_annotation'),false);
    press('편집'); press('삭제'); await press('삭제 확인');
    assert.equal(panel.querySelectorAll('li').length,0);
    assert.deepEqual(requests,['update_annotation','get_annotation','update_annotation','delete_annotation']);
  });
}

test('다음 근거 페이지의 기록을 삭제하면 건수가 줄고 늦은 조회가 기록을 복원하지 않는다', async () => {
  const {app, el} = setup();
  const e = evidence('e1', {annotation_start:0, annotation_cursor:'next', annotation_count:2,
    annotations:[{annotation_id:'kept', kind:'note', body:'유지'}]});
  const record = {annotation_id:'old', context_id:'ctx', kind:'note', body:'삭제 대상', revision:1,
    segments:[{target:'original', chunk_id:e.chunk_id, parse_revision_id:'p', start_offset:0,
      end_offset:e.quote_original.length, quote_snapshot:e.quote_original}]};
  let finish;
  app.callServerTool = async call => {
    if (call.name === 'list_evidence_annotations') return new Promise(resolve => { finish = resolve; });
    assert.equal(call.name, 'delete_annotation');
    return {structuredContent:{outcome:'deleted', annotation:null}};
  };
  app.ontoolresult(detailPayload({evidence:[e], annotation_page:{context_id:'ctx', items:[record], next_cursor:null}}));
  const pending = el('more-records').onclick();
  const press = label => [...el('context-records').querySelectorAll('button')].find(b => b.textContent === label).onclick();
  press('편집'); press('삭제'); await press('삭제 확인');
  finish({structuredContent:{context_id:'ctx', evidence_id:'e1', annotation_start:0,
    annotation_cursor:null, annotation_count:2, annotations:[record]}});
  await pending;
  assert.match(el('record-status').textContent,/전체 1건/);
  assert.doesNotMatch(el('records').textContent,/삭제 대상/);
  assert.equal(el('more-records').disabled,false);
});
