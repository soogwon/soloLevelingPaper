// 미확정 요청은 재시작 뒤에도 같은 키를 사용한다. 본문·인용문 대신 SHA-256만 보관한다.
export async function prepareAnnotationRequest(request, storage, crypto) {
  const bytes = new TextEncoder().encode(JSON.stringify(request));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  const slot = 'solo-annotation-request-v1:' + hash;
  let key = storage.getItem(slot);
  if (key !== null && !/^[A-Za-z0-9_-]{1,128}$/.test(key)) throw new Error();
  if (key === null) {
    key = crypto.randomUUID();
    storage.setItem(slot, key);
  }
  // 저장 제한·실패 시 키 없는 요청을 전송하지 않는다.
  if (storage.getItem(slot) !== key) throw new Error();
  return {
    request: {...request, request_key: key},
    confirm() {
      if (storage.getItem(slot) === key) storage.removeItem(slot);
    },
  };
}
