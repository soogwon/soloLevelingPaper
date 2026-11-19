import assert from 'node:assert/strict';
import test from 'node:test';
globalThis.window = {location:{search:''}};
const {compactAnnotationCard} = await import('../js/lib/annotation.js');
test('MCP 전송 경로로 작성 주체를 추정하지 않고 저장된 미검증 출처만 표시한다', () => {
  const base = {annotation_id:'a',kind:'note',body:'메모',segments:[],author_type:'mcp'};
  assert.match(String(compactAnnotationCard(base)), /저장된 작성 출처: 확인되지 않음 \(미검증\)/);
  assert.doesNotMatch(String(compactAnnotationCard(base)), /AI 생성/);
  assert.match(String(compactAnnotationCard({...base,creation_origin:'user'})), /저장된 작성 출처: 사용자 입력 \(미검증\)/);
  assert.match(String(compactAnnotationCard({...base,creation_origin:'ai'})), /저장된 작성 출처: AI 생성 \(미검증\)/);
});

test('저장 인용과 현재 본문 일치 검증 상태를 구분한다', () => {
  const base = {annotation_id:'a',kind:'highlight',body:'',creation_origin:'user'};
  const unchecked = String(compactAnnotationCard({...base,segments:[{
    target:'original',quote:'근거',text_match_verified:false,
  }]}));
  assert.match(unchecked, /저장 당시 인용 · 현재 본문 일치 미확인/);

  const verified = String(compactAnnotationCard({...base,segments:[{
    target:'translation',quote:'번역 근거',text_match_verified:true,
  }]}));
  assert.match(verified, /한국어 번역 · 현재 본문 일치 확인/);
});

test('원격 MCP 기록과 로컬 동기화 기록의 저장 출처를 구분한다', () => {
  const base = {annotation_id:'a',kind:'note',body:'메모',segments:[]};
  assert.match(String(compactAnnotationCard({...base,storage_origin:'remote'})), /원격 MCP 저장/);
  assert.match(String(compactAnnotationCard({...base,storage_origin:'local_sync'})), /로컬 동기화/);
});
