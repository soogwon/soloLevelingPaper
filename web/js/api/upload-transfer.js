// 서명 URL은 서버 세션 API와 분리한다. 쿠키·CSRF·참조 URL을 저장소로 보내지 않는다.
export async function putRemoteUpload(prepared, body, signal) {
  let url, headers;
  try {
    url = new URL(prepared.upload_url);
    if (url.protocol !== 'https:' || url.username || url.password || url.hash
        || prepared.method !== 'PUT' || !(body instanceof Blob) || body.type !== 'application/pdf'
        || !prepared.headers || typeof prepared.headers !== 'object') throw new Error();
    headers = {};
    for (const [name,value] of Object.entries(prepared.headers)) {
      const lower = name.toLowerCase();
      if (!['content-type','content-length'].includes(lower) || typeof value !== 'string'
          || Object.hasOwn(headers,lower)) throw new Error();
      headers[lower] = value;
    }
    if (headers['content-type'] !== body.type || headers['content-length'] !== String(body.size)) throw new Error();
    // Content-Length는 브라우저가 Blob 길이로 생성하는 금지 요청 헤더다.
    delete headers['content-length'];
  } catch { throw new Error('invalid_upload_destination'); }
  try {
    const response = await fetch(url.href, {method:'PUT', headers, body, signal,
      credentials:'omit', redirect:'error', referrerPolicy:'no-referrer', mode:'cors', cache:'no-store'});
    if (!response.ok) throw new Error();
  } catch {
    // 서명 URL·저장소 오류 본문을 화면과 로그에 노출하지 않는다.
    throw new Error('upload_result_unknown');
  }
}
