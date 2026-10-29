import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { mountDetailApp } from './detail.js';
const html = await readFile(new URL('./template.html', import.meta.url),'utf8');
const evidence = (id='e1', changes={}) => ({evidence_id:id,chunk_id:`c-${id}`,version_id:'v',parse_revision_id:'p',translation_revision_id:'t',pdf_page:6,printed_page_label:null,quote_original:'A is faster when',quote_ko:'A가 빠른 조건',...changes});
function setup(connect=async()=>{},timeout=1000){
 const doc=new JSDOM(html).window.document;
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
