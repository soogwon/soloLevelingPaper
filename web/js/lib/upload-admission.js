// 가입 때 두 동의를 받은 계정은 업로드 검증이 끝나면 학습 처리 접수까지 이어서 한다.
// 서버가 계정 동의로 이 업로드의 목적별 동의를 기록하므로 동의를 다시 묻지 않는다.
export const admissionMessages = {
  pending: ' 학습 자료 만들기를 접수 중이에요.',
  admitted: ' 학습 자료 만들기를 접수했어요. 아래에서 처리 상태를 확인할 수 있어요.',
  consent_missing: ' OpenAI 전송 동의를 확인하지 못해 학습 자료 만들기를 접수하지 않았어요. 계정 화면에서 동의 상태를 확인해 주세요.',
  quota: ' 이 계정이 만들 수 있는 학습 자료 수에 도달해 학습 자료 만들기를 접수하지 않았어요.',
  failed: ' 학습 자료 만들기를 접수하지 못했어요. 논문 상세의 ‘Claude에서 학습 시작’에서 다시 접수할 수 있어요.',
};

export async function admitAfterUpload({api, consents, intentId, key}) {
  consents.bind({intent_id:intentId, status:'verified'});
  await consents.refresh();
  const {status, values} = consents.snapshot();
  if (status !== 'ready' || values.storage?.state !== 'current' || values.ai_provider?.state !== 'current') {
    return 'consent_missing';
  }
  try {
    const job = await api.admitRemoteProcessing(intentId, {
      storage_consent_id: values.storage.consent_id,
      ai_consent_id: values.ai_provider.consent_id,
    }, key);
    return job && typeof job.job_id === 'string' ? 'admitted' : 'failed';
  } catch (error) {
    if (error?.status === 409 && error.code === 'remote_processing_quota_exceeded') return 'quota';
    return 'failed';
  }
}
