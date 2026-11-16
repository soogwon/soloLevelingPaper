import assert from 'node:assert/strict';
import test from 'node:test';

import { validateRemoteUploadConsentRace } from '../js/validation/remote-upload-consent-race.js';

const INTENT_ID = '11111111-1111-4111-8111-111111111111';
const CONSENT_ID = '22222222-2222-4222-8222-222222222222';
const STAGING_ORIGIN = 'https://solo-leveling-staging.soogyung.com';

const input = values => ({ origin: STAGING_ORIGIN, ...values });

function pdf() {
  return new File(['%PDF-1.4\nsynthetic'], 'public-synthetic.pdf', { type: 'application/pdf' });
}

function fixture(overrides = {}) {
  const calls = [];
  let state = 'current';
  const api = {
    async getRemoteUploadIntent(id) {
      calls.push(['intent', id]);
      return { intent_id: id, status: 'uploading' };
    },
    async getRemoteUploadConsents(id) {
      calls.push(['consents', id]);
      return { intent_id: id, storage: {
        purpose: 'storage', state, consent_id: CONSENT_ID, revision: 1,
      } };
    },
    async prepareRemoteUpload(id) {
      calls.push(['prepare', id]);
      return { intent_id: id, method: 'PUT', upload_url: 'https://r2.example/signed',
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        headers: { 'Content-Type': 'application/pdf', 'Content-Length': '24' } };
    },
    async revokeRemoteUploadConsent(id, purpose, revision) {
      calls.push(['revoke', id, purpose, revision]);
      state = 'revoked';
      return { purpose, state, consent_id: CONSENT_ID, revision };
    },
    async putRemoteUpload(prepared, body) {
      calls.push(['put', prepared.intent_id, body.size]);
    },
    async completeRemoteUpload(id) {
      calls.push(['complete', id]);
      throw { status: 409, code: 'remote_upload_storage_consent_required' };
    },
    ...overrides,
  };
  return { api, calls };
}

test('prepare 뒤 철회, PUT, complete 409, 재조회 순서를 고정한다', async () => {
  const { api, calls } = fixture();
  const result = await validateRemoteUploadConsentRace(input({
    api, file: pdf(), intentId: INTENT_ID, confirmPrepared: async safe => {
      assert.deepEqual(Object.keys(safe).sort(),
        ['expires_at', 'file_name', 'file_size', 'intent_id', 'method']);
      return true;
    },
  }));

  assert.deepEqual(calls.map(call => call[0]),
    ['intent', 'consents', 'prepare', 'consents', 'revoke', 'put', 'complete', 'consents', 'intent']);
  assert.deepEqual(result, {
    intent_id: INTENT_ID,
    intent_status: 'uploading',
    storage_consent_state: 'revoked',
    put_completed: true,
    complete_status: 409,
    complete_code: 'remote_upload_storage_consent_required',
    final_object_published: false,
  });
  assert.equal(JSON.stringify(result).includes('signed'), false);
});

test('prepare 확인을 거부하면 철회와 PUT을 시작하지 않는다', async () => {
  const { api, calls } = fixture();
  await assert.rejects(validateRemoteUploadConsentRace(input({
    api, file: pdf(), intentId: INTENT_ID, confirmPrepared: async () => false,
  })), /cancelled_before_revoke/);
  assert.deepEqual(calls.map(call => call[0]), ['intent', 'consents', 'prepare', 'consents']);
});

test('complete가 성공하거나 다른 오류이면 검증 성공으로 오인하지 않는다', async () => {
  const { api } = fixture({ completeRemoteUpload: async () => ({ status: 'verified' }) });
  await assert.rejects(validateRemoteUploadConsentRace(input({
    api, file: pdf(), intentId: INTENT_ID, confirmPrepared: async () => true,
  })), /complete_was_not_rejected/);
});

test('철회 전 동의 revision이 바뀌면 전송하지 않는다', async () => {
  let reads = 0;
  const { api, calls } = fixture({
    async getRemoteUploadConsents(id) {
      reads += 1;
      calls.push(['consents', id]);
      return { intent_id: id, storage: { purpose: 'storage', state: 'current',
        consent_id: CONSENT_ID, revision: reads } };
    },
  });
  await assert.rejects(validateRemoteUploadConsentRace(input({
    api, file: pdf(), intentId: INTENT_ID, confirmPrepared: async () => true,
  })), /storage_consent_changed_before_revoke/);
  assert.equal(calls.some(call => call[0] === 'put'), false);
});

test('staging 이외 origin에서는 prepare를 시작하지 않는다', async () => {
  const { api, calls } = fixture();
  await assert.rejects(validateRemoteUploadConsentRace({
    api, file: pdf(), intentId: INTENT_ID, origin: 'https://example.com',
    confirmPrepared: async () => true,
  }), /invalid_input/);
  assert.deepEqual(calls, []);
});
