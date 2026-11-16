import { api } from '../api/index.js';
import { CONFIG } from '../config.js';
import { html, setHTML } from '../lib/html.js';
import { createUploadIntent, uploadMessages } from '../lib/upload-intent.js';

export default function uploadPage({ view }) {
  document.title = '새 PDF 등록 준비 · Solo Leveling';
  setHTML(view, html`<h1 class="page-title">새 PDF 등록 준비</h1>
    <section class="card panel">
      <p>파일명·크기·형식·SHA-256 파일 지문을 로그인한 계정의 서버에 보내 등록을 준비해요.
      이 단계에서 PDF 본문을 전송하거나 AI를 호출하지 않아요.</p>
      <label>PDF 선택 <input type="file" accept=".pdf,application/pdf" data-file></label>
      <p class="hint">최대 50 MiB. 파일 재선택·취소 시 이전 요청 표시는 초기화돼요. 서버 기록이 삭제되지는 않아요.</p>
      <button class="btn" type="button" data-clear>선택 취소</button>
      <p data-file-label></p>
      <label><input type="checkbox" data-agree> 파일 정보와 파일 지문을 서버로 보내는 데 동의해요.</label>
      <div><button class="btn" type="button" data-create disabled>등록 준비 저장</button>
      <button class="btn" type="button" data-read disabled>기존 요청 상태 확인</button></div>
      <p role="status" aria-live="polite" data-status></p><p data-intent></p>
      <p>PDF 보관 동의 연결은 준비 중이에요. 현재는 파일 업로드·AI 전송·학습 처리를 시작할 수 없어요.</p>
      <button class="btn" type="button" disabled>PDF 업로드 (준비 중)</button>
      <p class="hint">새로고침·화면 이동·로그아웃 시 이 화면의 요청 키는 지워져요. 결과가 미확정이면
      화면을 유지하고 같은 요청을 확인하세요. 새로 파일을 선택하면 별도 등록 준비가 생길 수 있어요.</p>
    </section>`);
  const input = view.querySelector('[data-file]'), agree = view.querySelector('[data-agree]');
  const create = view.querySelector('[data-create]'), read = view.querySelector('[data-read]');
  const controller = createUploadIntent({ api, changed: draw });
  function draw(state) {
    if (!view.isConnected) return;
    view.querySelector('[data-file-label]').textContent = state.file ? `${state.file.name} · ${state.file.size} bytes` : '';
    view.querySelector('[data-status]').textContent = CONFIG.USE_MOCK ? '예시 모드에서는 원격 등록 준비를 실행하지 않아요.' : uploadMessages[state.status];
    view.querySelector('[data-intent]').textContent = state.intent ? `업로드 의도 ID: ${state.intent.intent_id}` : '';
    create.disabled = CONFIG.USE_MOCK || !state.canCreate || !agree.checked;
    create.textContent = state.status === 'uncertain' && !state.intent ? '같은 등록 준비 요청 확인' : '등록 준비 저장';
    read.disabled = CONFIG.USE_MOCK || !state.canRead;
  }
  const select = () => { agree.checked = false; return controller.select(input.files?.[0] ?? null); };
  input.addEventListener('change', select);
  input.addEventListener('cancel', () => { input.value = ''; select(); });
  view.querySelector('[data-clear]').addEventListener('click', () => { input.value = ''; select(); });
  agree.addEventListener('change', () => draw(controller.snapshot()));
  create.addEventListener('click', () => { if (view.isConnected && !CONFIG.USE_MOCK) return controller.run(agree.checked); });
  read.addEventListener('click', () => { if (view.isConnected && !CONFIG.USE_MOCK) return controller.run(); });
  const events = ['auth:expired','auth:logout','hashchange','pagehide'];
  function dispose() {
    events.forEach(event => window.removeEventListener(event, dispose));
    input.value = ''; agree.checked = false; controller.close();
  }
  events.forEach(event => window.addEventListener(event, dispose));
  draw(controller.snapshot());
}
