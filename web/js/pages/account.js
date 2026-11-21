// 7. 계정·연결 기기 — GET /v1/me, GET /v1/devices, DELETE /v1/devices/{id}, POST /v1/auth/logout
import { api } from '../api/index.js';
import { html, setHTML } from '../lib/html.js';
import { navigate } from '../router.js';
import { icons, loadingBlock, emptyBlock, errorBlock, errorMessage, confirmDialog, toast } from '../lib/ui.js';
import { formatDate, formatDateTime } from '../lib/format.js';

/** 'a@b.c' 형태인지 확인한다 (display_name에 이메일이 들어온 경우를 가려내는 용도). */
function isEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

/** 이메일 일부를 가린다: demo@example.com → de***@example.com */
function maskEmail(email) {
  if (!email || !email.includes('@')) return '';
  const [local, domain] = email.split('@');
  return `${local.slice(0, 2)}***@${domain}`;
}

export default async function accountPage({ view }) {
  document.title = '계정·연결 기기 · Solo Leveling';
  const me = await api.me();
  // 서버가 display_name에 이메일을 그대로 보내는 경우가 있다. 그때는 이름 자리에도 가린 이메일을 쓰고
  // 아래 줄(가린 이메일)은 같은 내용이라 숨긴다.
  const rawName = me.display_name?.trim() ?? '';
  const nameIsEmail = isEmail(rawName);
  const name = nameIsEmail ? maskEmail(rawName) : (rawName || '이름 없음');
  const subline = nameIsEmail ? '' : maskEmail(me.email);

  setHTML(view, html`
    <h1 class="page-title">계정 · 연결 기기</h1>

    <section class="card profile" aria-labelledby="acc-h">
      <div class="profile-main">
        <span class="profile-avatar" aria-hidden="true">${[...name][0]}</span>
        <div>
          <h2 id="acc-h" class="panel-title">${name}</h2>
          ${subline ? html`<p class="meta">${subline}</p>` : ''}
        </div>
      </div>
      <button type="button" class="btn" data-action="logout">로그아웃</button>
    </section>

    <section class="card panel" aria-labelledby="remote-mcp-h">
      <h2 id="remote-mcp-h" class="panel-title">원격 MCP 연결</h2>
      <div data-slot="remote-connections" class="stack">${loadingBlock()}</div>
      <div data-slot="remote-check" aria-live="polite"></div>
    </section>

    <div data-slot="devices"></div>

    <section class="card panel" aria-labelledby="up-h">
      <div>
        <h2 id="up-h" class="panel-title">서버에 올리는 기록</h2>
        <p class="meta" style="margin-top:6px">업로드 범위 동의는 Claude의 MCP 설정에서 바꿀 수 있어요.</p>
      </div>
      <div class="scope-grid">
        <div class="scope">
          <span class="badge b-ok" style="align-self:flex-start">올라가는 기록</span>
          <ul><li>선택한 논문의 정보</li><li>학습 위치와 읽기 상태</li><li>저장된 요약</li><li>메모·하이라이트 (선택한 원문 문장 포함)</li><li>PDF 원본 — 논문별로 따로 선택</li></ul>
        </div>
        <div class="scope">
          <span class="badge b-gray" style="align-self:flex-start">올라가지 않는 기록</span>
          <ul><li>논문 전체 추출 원문·번역문</li><li>검색용 벡터 데이터</li><li>내 PC의 파일 경로</li><li>API 키</li></ul>
        </div>
      </div>
    </section>`);

  const remoteEl = view.querySelector('[data-slot="remote-connections"]');

  async function loadRemoteConnections() {
    setHTML(remoteEl, loadingBlock());
    try {
      const connections = await api.listRemoteConnections();
      setHTML(remoteEl, connections.length
        ? html`<ul class="device-list">${connections.map((connection) => html`<li class="device">
            <div>
              <div class="device-name">원격 MCP</div>
              <div class="meta">연결됨 · ${formatDateTime(connection.linked_at)}</div>
            </div>
          </li>`)}</ul>
          <div class="actions"><button type="button" class="btn" data-action="issue-remote-check">이 Claude 연결 확인</button></div>`
        : emptyBlock('원격 MCP 연결이 없어요', 'Claude에서 원격 MCP에 연결하고 첫 논문 조회를 실행해 주세요.'));
    } catch (error) {
      setHTML(remoteEl, errorBlock(error, 'reload-remote-connections'));
    }
  }

  const checkEl = view.querySelector('[data-slot="remote-check"]');
  let currentCheck = null;

  async function issueRemoteCheck(button) {
    button.disabled = true;
    currentCheck = null;
    setHTML(checkEl, loadingBlock());
    try {
      const issued = await api.createRemoteConnectionCheck();
      if (!view.isConnected) return;
      currentCheck = issued.check_id;
      setHTML(checkEl, html`
        <p>확인할 Claude 대화에서 아래 요청을 보내세요. 코드는 5분 동안 사용할 수 있어요.</p>
        <p><code>Solo Leveling 원격 MCP의 verify_remote_connection에 코드 ${issued.code}를 보내 주세요.</code></p>
        <button type="button" class="btn" data-action="remote-check-status">확인 결과 새로고침</button>
        <p data-slot="check-status"></p>`);
    } catch (error) {
      setHTML(checkEl, html`<p>${errorMessage(error)}</p>`);
    } finally {
      button.disabled = false;
    }
  }

  async function refreshRemoteCheck(button) {
    const checkId = currentCheck;
    if (!checkId) return;
    const statusEl = checkEl.querySelector('[data-slot="check-status"]');
    button.disabled = true;
    try {
      const check = await api.remoteConnectionCheckStatus(checkId);
      if (currentCheck !== checkId) return;
      if (check.status === 'verified') {
        currentCheck = null;
        setHTML(checkEl, html`<p>이 Claude에서 연결 확인됨 · ${formatDateTime(check.verified_at)}</p>`);
      } else if (check.status === 'expired') {
        currentCheck = null;
        setHTML(checkEl, html`<p>코드가 만료됐어요. 연결 확인을 다시 시작해 주세요.</p>`);
      } else {
        setHTML(statusEl, '아직 확인 요청을 받지 못했어요.');
      }
    } catch (error) {
      if (currentCheck === checkId) setHTML(statusEl, errorMessage(error));
    } finally {
      button.disabled = false;
    }
  }

  const devicesEl = view.querySelector('[data-slot="devices"]');
  let devices = [];

  function renderDevices() {
    const wasOpen = devicesEl.querySelector('details')?.open;
    setHTML(devicesEl, devices.length
      ? html`<details class="card panel processing-jobs" ${wasOpen ? html`open` : ''}>
        <summary class="panel-title">로컬 기기 (${devices.length})</summary>
        <ul class="device-list">${devices.map((d) => html`<li class="device">
          <div class="profile-main">
            ${icons.laptop()}
            <div>
              <div class="device-name">${d.name}</div>
              <div class="meta">등록 상태 연결됨 · 연결 ${formatDate(d.connected_at)} · 최근 활동 ${
                d.last_active_available
                  ? (d.last_active_at ? formatDateTime(d.last_active_at) : '기록 없음')
                  : '정보 미제공'
              }</div>
            </div>
          </div>
          <button type="button" class="btn btn-outline-danger" data-action="disconnect" data-device-id="${d.device_id}" data-device-name="${d.name}">연결 해제</button>
        </li>`)}</ul>
        <p class="hint">연결을 해제해도 서버에 저장된 기록은 남아요.</p>
      </details>` : '');
  }

  async function loadDevices() {
    setHTML(devicesEl, '');
    try {
      devices = (await api.listDevices()).items;
      renderDevices();
    } catch (error) {
      setHTML(devicesEl, html`<section class="card panel">
        <h2 class="panel-title">로컬 기기 조회 실패</h2>
        ${errorBlock(error, 'reload-devices')}
      </section>`);
    }
  }

  view.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    if (el.dataset.action === 'issue-remote-check') await issueRemoteCheck(el);
    if (el.dataset.action === 'remote-check-status') await refreshRemoteCheck(el);
    if (el.dataset.action === 'reload-remote-connections') loadRemoteConnections();
    if (el.dataset.action === 'reload-devices') loadDevices();
    if (el.dataset.action === 'disconnect') {
      const ok = await confirmDialog({
        title: `‘${el.dataset.deviceName}’ 연결을 해제할까요?`,
        message: '이 PC의 MCP는 더 이상 서버 기록을 읽거나 저장할 수 없어요. 이미 저장된 기록은 그대로 남아요.',
        confirmLabel: '연결 해제',
        danger: true,
      });
      if (!ok) return;
      try {
        await api.disconnectDevice(el.dataset.deviceId);
        devices = devices.filter((d) => d.device_id !== el.dataset.deviceId);
        renderDevices();
        toast('연결을 해제했어요.');
      } catch (error) {
        toast(errorMessage(error));
      }
    }
    if (el.dataset.action === 'logout') {
      el.disabled = true;
      try {
        await api.logout();
        window.dispatchEvent(new CustomEvent('auth:logout'));
        navigate('/login', undefined, { replace: true });
      } catch (error) {
        el.disabled = false;
        toast(errorMessage(error));
      }
    }
  });

  await Promise.all([loadRemoteConnections(), loadDevices()]);
}
