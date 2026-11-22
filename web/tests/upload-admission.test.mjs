import test from 'node:test';
import assert from 'node:assert/strict';
import { admitAfterUpload, admissionMessages } from '../js/lib/upload-admission.js';

const intentId = '00000000-0000-0000-0000-0000000000a1';
const storage = {consent_id:'00000000-0000-0000-0000-0000000000b1', state:'current'};
const ai = {consent_id:'00000000-0000-0000-0000-0000000000b2', state:'current'};

function consents(values, status = 'ready') {
  const calls = [];
  return {calls, bind:value => calls.push(['bind', value]), refresh:async () => calls.push(['refresh']),
    snapshot:() => ({status, values})};
}

test('업로드가 검증되면 계정 동의로 기록된 동의로 처리 접수까지 한다', async () => {
  const c = consents({storage, ai_provider:ai}), sent = [];
  const api = {admitRemoteProcessing:async (...args) => { sent.push(args); return {job_id:'job', status:'queued'}; }};
  assert.equal(await admitAfterUpload({api, consents:c, intentId, key:'key-1'}), 'admitted');
  assert.deepEqual(c.calls, [['bind', {intent_id:intentId, status:'verified'}], ['refresh']]);
  assert.deepEqual(sent, [[intentId, {storage_consent_id:storage.consent_id, ai_consent_id:ai.consent_id}, 'key-1']]);
});

test('AI 전송 동의가 현재가 아니면 접수하지 않는다', async () => {
  let called = false;
  const api = {admitRemoteProcessing:async () => { called = true; }};
  const result = await admitAfterUpload({api, consents:consents({storage, ai_provider:{...ai, state:'revoked'}}), intentId, key:'k'});
  assert.equal(result, 'consent_missing');
  assert.equal(called, false);
});

test('계정 한도와 그 밖의 접수 실패를 구분한다', async () => {
  const fail = error => ({admitRemoteProcessing:async () => { throw error; }});
  const c = consents({storage, ai_provider:ai});
  assert.equal(await admitAfterUpload({api:fail({status:409, code:'remote_processing_quota_exceeded'}), consents:c, intentId, key:'k'}), 'quota');
  assert.equal(await admitAfterUpload({api:fail({status:409, code:'remote_processing_not_ready'}), consents:c, intentId, key:'k'}), 'failed');
  assert.equal(await admitAfterUpload({api:fail(new Error('network')), consents:c, intentId, key:'k'}), 'failed');
  for (const key of ['pending', 'admitted', 'consent_missing', 'quota', 'failed']) assert.ok(admissionMessages[key]);
});
