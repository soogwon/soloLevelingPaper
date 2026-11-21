// 서버 논문 삭제 흐름 — 논문 상세(주 경로)와 논문 목록(보조 경로)이 함께 쓴다.
// 1) 삭제 범위·안내 확인(체크) → 2) "정말 삭제하시겠습니까?" 재확인 → 3) PUT 삭제 접수.
// 요청 키는 흐름마다 한 번만 만들고, 응답을 확인하지 못해 다시 보낼 때도 같은 키를 쓴다 (doc/setup/server-paper-deletion.md).
import { api } from '../api/index.js';
import { ApiError } from '../api/client.js';
import { html } from './html.js';
import { openDialog, confirmDialog, errorMessage, newRequestKey } from './ui.js';

/** doc/architecture/26-deletion-sync-policy.md '사용자 안내' 문구. notice_version과 함께 바꾼다. */
export const PAPER_DELETION_NOTICE = '삭제 요청 즉시 해당 자료에 대한 접근과 처리가 중단됩니다. 활성 저장소와 백업을 포함한 완전 삭제는 최대 30일이 걸릴 수 있습니다. 삭제된 데이터가 복원이나 지연된 작업으로 다시 생성되지 않도록 최소한의 삭제 표식은 추가로 최대 30일간 보관됩니다.';

export const PAPER_DELETION_SCOPE = [
  '서버에 저장된 이 논문의 PDF 원문, 검색 데이터, 저장된 근거·메모 등이 삭제돼요.',
  '삭제 요청 후에는 되돌릴 수 없어요. 다시 쓰려면 PDF를 새로 등록해야 해요.',
  '연결된 PC에 있는 로컬 논문·PDF는 지우지 않아요.',
];

/** 응답을 확인하지 못한 경우(네트워크 끊김·서버 일시 오류)만 같은 키로 다시 보낼 수 있다. */
export function isUncertainDeletionError(error) {
  return error instanceof ApiError && (error.status === 0 || error.status >= 500);
}

/**
 * 삭제를 접수한다. 결과가 불확실하면 askRetry()가 true일 때 같은 요청 키로 다시 보낸다.
 * → 서버 응답 또는 null(사용자가 재전송을 그만둠). 확정 오류(404·409 등)는 그대로 던진다.
 */
export async function submitPaperDeletion({ paperId, requestKey, askRetry, request = api.requestPaperDeletion }) {
  for (;;) {
    try {
      return await request(paperId, requestKey);
    } catch (error) {
      if (!isUncertainDeletionError(error)) throw error;
      if (!(await askRetry(error))) return null;
    }
  }
}

function noticeDialog(title) {
  return new Promise((resolve) => {
    let result = false;
    const dialog = openDialog({
      title: '서버에서 논문 삭제',
      role: 'alertdialog',
      body: html`
        <p class="dialog-text"><strong>${title}</strong></p>
        <ul class="dialog-list">${PAPER_DELETION_SCOPE.map((line) => html`<li>${line}</li>`)}</ul>
        <p class="dialog-text hint">${PAPER_DELETION_NOTICE}</p>
        <label class="check-row"><input type="checkbox" data-ack> 위 내용을 확인했고, 이 논문을 서버에서 삭제하려고 해요.</label>
        <div class="dialog-actions">
          <button type="button" class="btn" data-dialog-close>취소</button>
          <button type="button" class="btn btn-danger" data-confirm disabled>삭제</button>
        </div>`,
    });
    const ack = dialog.querySelector('[data-ack]');
    const confirm = dialog.querySelector('[data-confirm]');
    ack.addEventListener('change', () => { confirm.disabled = !ack.checked; });
    confirm.addEventListener('click', () => { if (ack.checked) { result = true; dialog.close(); } });
    dialog.addEventListener('close', () => resolve(result));
  });
}

/**
 * 화면에서 부르는 전체 흐름. → 접수된 응답 또는 null(취소·중단).
 * 확정 오류는 그대로 던지고, 부른 화면이 toast로 보여 준다.
 */
export async function runPaperDeletionFlow({ paperId, title }) {
  if (!(await noticeDialog(title))) return null;
  const sure = await confirmDialog({
    title: '정말 삭제하시겠습니까?',
    message: `‘${title}’ 논문을 서버에서 삭제해요. 이 작업은 되돌릴 수 없어요.`,
    confirmLabel: '삭제',
    danger: true,
  });
  if (!sure) return null;
  const requestKey = newRequestKey();
  return submitPaperDeletion({
    paperId,
    requestKey,
    askRetry: (error) => confirmDialog({
      title: '삭제 요청 결과를 확인하지 못했어요',
      message: `${errorMessage(error)} 같은 요청으로 다시 보낼까요? 이미 접수됐다면 중복으로 처리되지 않아요.`,
      confirmLabel: '다시 보내기',
    }),
  });
}

/** 접수 직후 안내. 서버는 접수 즉시 논문을 목록·상세에서 숨긴다. */
export function deletionAcceptedMessage(result) {
  return result?.status === 'complete'
    ? '논문을 서버에서 삭제했어요. 백업까지 지워지는 데는 최대 30일이 걸릴 수 있어요.'
    : '삭제를 요청했어요. 접근은 바로 중단되고, 완전 삭제는 최대 30일이 걸릴 수 있어요.';
}
