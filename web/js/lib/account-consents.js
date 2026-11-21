// 계정 단위 동의: 첫 로그인 화면과 계정 설정에서 함께 쓴다.
import { html } from './html.js';

export const purposeLabels = {
  storage: 'PDF와 학습 기록 서버 저장',
  ai_provider: '논문 원문·질문의 OpenAI(미국) 전송',
};
const stateLabels = { current: '동의함', missing: '동의하지 않음', revoked: '철회함', stale: '고지 변경 · 다시 동의 필요' };

const PURPOSES = Object.keys(purposeLabels);

// 로그인 뒤 한 번 읽은 계정 동의. 화면은 추가 요청 없이 이 값으로 간단한 흐름을 고른다.
export const accountConsents = { value: null };

/** 목적별 계정 동의가 현재인지. 알 수 없으면 false로 두어 기존 동의 단계를 보여준다. */
export function accountConsentCurrent(purpose) {
  return accountConsents.value?.items?.some((item) => item.purpose === purpose && item.state === 'current') ?? false;
}

/** 서버 응답 형식을 확인한다. 알 수 없는 응답으로 화면을 그리지 않는다. */
export function checkedConsents(value) {
  if (!value || typeof value.notice_version !== 'string' || typeof value.complete !== 'boolean'
      || !Array.isArray(value.items)
      || value.items.map((item) => item?.purpose).join() !== PURPOSES.join()
      || value.items.some((item) => typeof item.notice !== 'string' || typeof item.required !== 'boolean'
        || !Object.hasOwn(stateLabels, item.state))) {
    throw new Error('invalid_account_consents');
  }
  return value;
}

export function consentItem(item, control) {
  return html`<div class="consent-item">
    <div class="consent-head">
      <strong>${purposeLabels[item.purpose]}</strong>
      <span class="badge ${item.required ? 'b-reading' : 'b-gray'}">${item.required ? '필수' : '선택'}</span>
      <span class="badge ${item.state === 'current' ? 'b-ok' : 'b-gray'}">${stateLabels[item.state]}</span>
    </div>
    <p class="meta">${item.notice}</p>
    ${control}
  </div>`;
}

export function announceConsents(value) {
  accountConsents.value = value;
  window.dispatchEvent(new CustomEvent('consents:changed', { detail: value }));
}
