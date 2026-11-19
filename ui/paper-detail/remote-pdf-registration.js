export function mountRemotePdfRegistration(app, doc) {
  const button = doc.getElementById('open');
  const fallback = doc.getElementById('fallback');
  let url;
  let generation = 0;

  function reset() {
    ++generation;
    url = undefined;
    button.disabled = true;
    fallback.textContent = '';
  }

  app.ontoolinput = app.ontoolinputpartial = app.ontoolcancelled = reset;
  app.ontoolresult = result => {
    reset();
    try {
      const parsed = new URL(result.isError ? '' : result.structuredContent?.upload_url);
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password ||
          parsed.pathname !== '/' || parsed.search || parsed.hash !== '#/uploads') throw new Error();
      url = parsed.href;
      button.disabled = false;
    } catch {
      fallback.textContent = '등록 주소를 확인하지 못했습니다. 채팅의 도구 응답을 확인해 주세요.';
    }
  };

  button.onclick = async () => {
    if (button.disabled || !url) return;
    const current = generation;
    button.disabled = true;
    try {
      const result = await app.openLink({url});
      if (result?.isError) throw new Error();
    } catch {
      if (current === generation) fallback.textContent = `브라우저에서 이 주소를 열어 주세요. ${url}`;
    } finally {
      if (current === generation) button.disabled = false;
    }
  };
  app.connect().catch(() => {
    reset();
    fallback.textContent = '카드를 연결하지 못했습니다. 채팅의 등록 주소를 이용해 주세요.';
  });
}
