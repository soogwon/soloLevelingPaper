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

    <section class="card panel" aria-labelledby="dev-h">
      <div>
        <h2 id="dev-h" class="panel-title">연결된 PC</h2>
        <p class="meta" style="margin-top:6px">이 계정으로 기록을 저장·조회하는 로컬 MCP 환경이에요. 새 PC는 MCP 설정에서 ‘계정 연결’을 실행하면 추가돼요.</p>
      </div>
      <div data-slot="devices">${loadingBlock()}</div>
      <p class="hint">연결을 해제한 PC는 더 이상 기록을 읽거나 저장할 수 없어요. 서버에 저장된 기록은 지워지지 않아요.</p>
    </section>

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

  const devicesEl = view.querySelector('[data-slot="devices"]');
  let devices = [];

  function renderDevices() {
    setHTML(devicesEl, devices.length
      ? html`<ul class="device-list">${devices.map((d) => html`<li class="device">
          <div class="profile-main">
            ${icons.laptop()}
            <div>
              <div class="device-name">${d.name}</div>
              <div class="meta">연결 ${formatDate(d.connected_at)} · 최근 활동 ${d.last_active_at ? formatDateTime(d.last_active_at) : '기록 없음'}</div>
            </div>
          </div>
          <button type="button" class="btn btn-outline-danger" data-action="disconnect" data-device-id="${d.device_id}" data-device-name="${d.name}">연결 해제</button>
        </li>`)}</ul>`
      : emptyBlock('연결된 PC가 없어요', 'MCP 설정에서 계정 연결을 실행해 주세요.'));
  }

  async function loadDevices() {
    setHTML(devicesEl, loadingBlock());
    try {
      devices = (await api.listDevices()).items;
      renderDevices();
    } catch (error) {
      setHTML(devicesEl, errorBlock(error, 'reload-devices'));
    }
  }

  view.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
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

  await loadDevices();
}
