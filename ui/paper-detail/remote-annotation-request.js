import { prepareAnnotationRequest } from './annotation-request.js';

// C 내부 포트다. 인증된 서비스·계정·세션 세대는 호스트 adapter만 제공한다.
// 공개 원격 도구나 서버의 멱등성·소유권 검사를 대신하지 않는다.
export function createRemoteAnnotationRecovery({storage, crypto, locks, currentScope}) {
  const fail = () => { throw new Error('ANNOTATION_RECOVERY_UNAVAILABLE'); };
  const valid = value => typeof value === 'string' && value.trim().length > 0;
  function scope() {
    const value = currentScope();
    if (!value || !['service', 'owner', 'session'].every(k => valid(value[k]))) fail();
    return {service:value.service, owner:value.owner, session:value.session};
  }
  function same(expected) {
    const actual = scope();
    if (Object.keys(expected).some(k => expected[k] !== actual[k])) fail();
  }
  // 객체 필드 순서만 정렬한다. 본문·공백·구간 순서·생략된 필드는 바꾸지 않는다.
  function canonical(value) {
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (Array.isArray(value)) {
      // 비어 있는 배열 칸을 JSON null로 바꿔 다른 입력과 같은 지문으로 만들지 않는다.
      for (let i = 0; i < value.length; i++) if (!Object.hasOwn(value, i)) fail();
      return value.map(canonical);
    }
    if (value && Object.getPrototypeOf(value) === Object.prototype) {
      return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    }
    fail();
  }
  return {
    async prepare(request, {allowNew = false} = {}) {
      try {
        const expected = scope();
        if (!locks?.request || !valid(request?.context_id) || Object.hasOwn(request, 'request_key')) fail();
        const input = canonical(request);
        const bytes = new TextEncoder().encode(JSON.stringify([expected.service, expected.owner]));
        const hash = await crypto.subtle.digest('SHA-256', bytes);
        const namespace = 'solo-remote-annotation-v1:' + Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2,'0')).join('') + ':';
        same(expected);
        // 여러 카드의 동시 키 발급을 막기 위해 같은 origin의 Web Locks를 사용한다.
        return await locks.request(namespace, async () => {
          same(expected);
          const scopedStorage = {
            getItem: key => storage.getItem(namespace + key),
            setItem: (key,value) => storage.setItem(namespace + key,value),
            removeItem: key => storage.removeItem(namespace + key),
          };
          const prepared = await prepareAnnotationRequest(input, scopedStorage, crypto, allowNew === true);
          same(expected);
          if (!prepared) return null;
          const payload = JSON.stringify(prepared.request);
          return {
            // 키 준비 후 계정이 바뀌어도 전송을 차단한다.
            async send(sender) {
              try {
                same(expected);
                const pending = await prepareAnnotationRequest(input, scopedStorage, crypto, false);
                same(expected);
                if (!pending || pending.request.request_key !== prepared.request.request_key) fail();
                const result = await sender(JSON.parse(payload));
                same(expected);
                return result;
              } catch { fail(); }
            },
            // 호출자가 같은 대상의 정상 응답을 검증한 뒤에만 확정한다.
            confirm() { try { same(expected); prepared.confirm(); } catch { fail(); } },
          };
        });
      } catch { fail(); }
    },
  };
}
