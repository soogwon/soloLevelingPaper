const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_PDF_BYTES = 50 * 1024 * 1024;
const STAGING_ORIGIN = 'https://solo-leveling-staging.soogyung.com';

class ValidationError extends Error {}

function invalid(message) {
  throw new ValidationError(`remote_upload_race_validation:${message}`);
}

function checkedIntent(value, expectedId) {
  if (!value || value.intent_id !== expectedId || !UUID.test(value.intent_id)
      || !['created', 'uploading'].includes(value.status)) {
    invalid('invalid_intent');
  }
  return value;
}

function checkedStorageConsent(value, expectedId, state) {
  const storage = value?.storage;
  if (value?.intent_id !== expectedId || storage?.purpose !== 'storage'
      || storage?.state !== state || !UUID.test(storage.consent_id)
      || !Number.isSafeInteger(storage.revision) || storage.revision < 1) {
    invalid(`storage_consent_${state}_required`);
  }
  return storage;
}

function checkedPrepared(value, expectedId) {
  if (!value || value.intent_id !== expectedId || value.method !== 'PUT'
      || typeof value.upload_url !== 'string' || !value.upload_url.startsWith('https://')
      || !Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now()
      || !value.headers || typeof value.headers !== 'object') {
    invalid('invalid_prepare');
  }
  return value;
}

async function checkedPdf(file) {
  if (!(file instanceof Blob) || typeof file.name !== 'string'
      || !/\.pdf$/i.test(file.name) || file.size <= 0 || file.size > MAX_PDF_BYTES
      || !['', 'application/pdf'].includes(file.type)) {
    invalid('invalid_pdf');
  }
  const bytes = await file.arrayBuffer();
  if (bytes.byteLength !== file.size
      || new TextDecoder().decode(bytes.slice(0, 5)) !== '%PDF-') {
    invalid('invalid_pdf');
  }
  return new Blob([bytes], { type: 'application/pdf' });
}

/**
 * 로그인된 staging 페이지의 기존 API 객체로만 실행하는 수동 경합 검증기다.
 * presigned URL, 쿠키, CSRF, owner/object key는 결과나 오류에 포함하지 않는다.
 */
export async function validateRemoteUploadConsentRace(options) {
  try {
    return await runValidation(options);
  } catch (error) {
    // 전송·파일·확인 콜백의 원본 오류에는 URL이나 자격 정보가 포함될 수 있다.
    if (error instanceof ValidationError) throw error;
    invalid('operation_failed');
  }
}

async function runValidation({ api, file, intentId, confirmPrepared,
  origin = globalThis.location?.origin }) {
  if (origin !== STAGING_ORIGIN || !api || typeof intentId !== 'string' || !UUID.test(intentId)
      || typeof confirmPrepared !== 'function') {
    invalid('invalid_input');
  }
  const body = await checkedPdf(file);
  checkedIntent(await api.getRemoteUploadIntent(intentId), intentId);
  const before = checkedStorageConsent(await api.getRemoteUploadConsents(intentId), intentId, 'current');
  const prepared = checkedPrepared(await api.prepareRemoteUpload(intentId), intentId);
  const rechecked = checkedStorageConsent(
    await api.getRemoteUploadConsents(intentId), intentId, 'current');
  if (before.consent_id !== rechecked.consent_id || before.revision !== rechecked.revision) {
    invalid('storage_consent_changed_before_revoke');
  }
  const approved = await confirmPrepared(Object.freeze({
    intent_id: intentId,
    method: prepared.method,
    expires_at: prepared.expires_at,
    file_name: file.name,
    file_size: file.size,
  }));
  if (approved !== true) invalid('cancelled_before_revoke');

  // 확인 창에서 오래 대기한 경우 철회·전송 전에 주소 만료를 다시 검사한다.
  checkedPrepared(prepared, intentId);
  const revoked = await api.revokeRemoteUploadConsent(intentId, 'storage', before.revision);
  if (revoked?.purpose !== 'storage' || revoked?.state !== 'revoked'
      || revoked?.consent_id !== before.consent_id || revoked?.revision !== before.revision) {
    invalid('invalid_revoke');
  }
  await api.putRemoteUpload(prepared, body);

  let rejection;
  try {
    await api.completeRemoteUpload(intentId);
  } catch (error) {
    rejection = error;
  }
  if (rejection?.status !== 409
      || rejection?.code !== 'remote_upload_storage_consent_required') {
    invalid('complete_was_not_rejected');
  }

  const after = checkedStorageConsent(
    await api.getRemoteUploadConsents(intentId), intentId, 'revoked');
  if (after.consent_id !== before.consent_id || after.revision !== before.revision) {
    invalid('storage_consent_changed_after_revoke');
  }
  const finalIntent = checkedIntent(await api.getRemoteUploadIntent(intentId), intentId);
  return Object.freeze({
    intent_id: intentId,
    intent_status: finalIntent.status,
    storage_consent_state: after.state,
    put_completed: true,
    complete_status: rejection.status,
    complete_code: rejection.code,
    complete_rejected: true,
    final_object_published: 'not_verified',
  });
}
