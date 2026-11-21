import test from 'node:test';
import assert from 'node:assert/strict';
const events=[];
globalThis.window={location:{search:'',origin:'https://example.test',hash:'#/consent'},
  addEventListener:()=>{},removeEventListener:()=>{},dispatchEvent:e=>events.push(e)};
globalThis.CustomEvent=class{constructor(type,init){this.type=type;this.detail=init?.detail;}};
globalThis.HashChangeEvent=class{constructor(type){this.type=type;}};
const replaced=[];
globalThis.history={state:null,replaceState:(_s,_t,url)=>replaced.push(url)};
globalThis.document={cookie:'csrf_token=synthetic'};
const {api}=await import('../js/api/index.js');
const {default:page}=await import('../js/pages/consent.js');
const {accountConsents,accountConsentCurrent}=await import('../js/lib/account-consents.js');

const item=(purpose,state)=>({purpose,required:purpose==='storage',notice:`<b>${purpose} 고지</b>`,state,revision:state==='current'?1:0,granted_at:null,revoked_at:null});
const status=(storage,ai)=>({notice_version:'account-consent-v1',complete:storage==='current',items:[item('storage',storage),item('ai_provider',ai)]});
function view(){
  const handlers={};
  return {isConnected:true,innerHTML:'',handlers,addEventListener(n,f){handlers[n]=f;}};
}
const target=(attrs)=>({...attrs,matches(s){return Object.keys(attrs.sel).some(k=>s===k);},closest(s){return s in attrs.sel?this:null;}});

test('필수 항목을 고르기 전에는 시작할 수 없고 선택 항목만 빼고 동의할 수 있다',async t=>{
  const grants=[];
  t.mock.method(api,'getAccountConsents',async()=>status('missing','missing'));
  t.mock.method(api,'grantAccountConsents',async(purposes,notice)=>{grants.push([purposes,notice]);return status('current','missing');});
  const v=view();await page({view:v,query:{return_to:'/uploads'}});
  assert.match(v.innerHTML,/data-submit disabled/);
  assert.match(v.innerHTML,/&lt;b&gt;storage 고지/);assert.doesNotMatch(v.innerHTML,/<b>storage/);
  assert.match(v.innerHTML,/\[필수\]/);assert.match(v.innerHTML,/\[선택\]/);
  v.handlers.change({target:target({sel:{'[data-purpose]':1},dataset:{purpose:'storage'},checked:true})});
  assert.doesNotMatch(v.innerHTML,/data-submit disabled/);
  await v.handlers.click({target:target({sel:{'[data-submit]':1}})});
  assert.deepEqual(grants,[[['storage'],'account-consent-v1']]);
  assert.equal(accountConsents.value.complete,true);
  assert.equal(accountConsentCurrent('storage'),true);assert.equal(accountConsentCurrent('ai_provider'),false);
  assert.equal(replaced.at(-1),'#/uploads');
  assert.ok(events.some(e=>e.type==='consents:changed'&&e.detail.complete));
});

test('전체 동의는 두 항목을 함께 보내고 외부 주소로는 돌아가지 않는다',async t=>{
  const grants=[];
  t.mock.method(api,'getAccountConsents',async()=>status('missing','missing'));
  t.mock.method(api,'grantAccountConsents',async(purposes)=>{grants.push(purposes);return status('current','current');});
  const v=view();await page({view:v,query:{return_to:'//evil.example'}});
  v.handlers.change({target:target({sel:{'[data-all]':1},checked:true})});
  await v.handlers.click({target:target({sel:{'[data-submit]':1}})});
  assert.deepEqual(grants,[['storage','ai_provider']]);
  assert.equal(replaced.at(-1),'#/papers');
});

test('고지가 바뀌면 다시 읽도록 안내하고 이동하지 않는다',async t=>{
  let reads=0;
  t.mock.method(api,'getAccountConsents',async()=>{reads++;return status('missing','missing');});
  t.mock.method(api,'grantAccountConsents',async()=>{throw Object.assign(new Error('changed'),{status:409});});
  const before=replaced.length;
  const v=view();await page({view:v,query:{}});
  v.handlers.change({target:target({sel:{'[data-purpose]':1},dataset:{purpose:'storage'},checked:true})});
  await v.handlers.click({target:target({sel:{'[data-submit]':1}})});
  assert.equal(reads,2);assert.equal(replaced.length,before);
  assert.match(v.innerHTML,/안내 내용이 바뀌었어요/);
});
