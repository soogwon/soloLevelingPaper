import assert from 'node:assert/strict';
import test from 'node:test';
globalThis.window = {location:{search:''}};
const {compactAnnotationCard} = await import('../js/lib/annotation.js');
test('MCP 전송 경로로 작성 주체를 추정하지 않고 최초 작성 신고만 표시한다', () => {
  const base = {annotation_id:'a',kind:'note',body:'메모',segments:[],author_type:'mcp'};
  assert.match(String(compactAnnotationCard(base)), /최초 생성 시 신고 주체: 미상/);
  assert.doesNotMatch(String(compactAnnotationCard(base)), /AI 생성/);
  assert.match(String(compactAnnotationCard({...base,creation_origin:'user'})), /최초 생성 시 신고 주체: 사용자 \(신고\)/);
  assert.match(String(compactAnnotationCard({...base,creation_origin:'ai'})), /최초 생성 시 신고 주체: AI \(신고\)/);
});
