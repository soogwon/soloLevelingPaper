// 컬렉션 이름 입력 대화상자 (생성·이름 수정 공용). 검증 규칙은 요청서 5.3을 따른다.
import { html } from '../lib/html.js';
import { openDialog, errorMessage } from '../lib/ui.js';

const HELP = '1~100자 · 같은 이름은 한 번만 쓸 수 있어요.';

/**
 * @param {{ title: string, submitLabel: string, initial?: string, onSubmit: (name: string) => Promise<void> }} options
 */
export function openNameDialog({ title, submitLabel, initial = '', onSubmit }) {
  const dialog = openDialog({
    title,
    body: html`<form class="stack" novalidate>
      <div class="field">
        <label for="collection-name">컬렉션 이름</label>
        <input id="collection-name" name="name" class="input" type="text" maxlength="100" autocomplete="off" value="${initial}" aria-describedby="collection-name-help" required>
        <p class="field-help" id="collection-name-help">${HELP}</p>
      </div>
      <div class="dialog-actions">
        <button type="button" class="btn" data-dialog-close>취소</button>
        <button type="submit" class="btn btn-primary">${submitLabel}</button>
      </div>
    </form>`,
  });

  const form = dialog.querySelector('form');
  const input = form.elements.name;
  const help = dialog.querySelector('#collection-name-help');
  const submit = form.querySelector('[type="submit"]');
  input.select();

  const showError = (message) => {
    input.setAttribute('aria-invalid', 'true');
    help.textContent = message;
    help.classList.add('error');
    input.focus();
  };
  input.addEventListener('input', () => {
    input.removeAttribute('aria-invalid');
    help.textContent = HELP;
    help.classList.remove('error');
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return showError('컬렉션 이름을 입력해 주세요.');
    if ([...name].length > 100) return showError('이름은 100자 이하로 입력해 주세요.');
    submit.disabled = true;
    try {
      await onSubmit(name);
      dialog.close();
    } catch (error) {
      // 이름 중복 등 서버 검증 오류는 입력칸 아래에 보여준다.
      showError(errorMessage(error));
    } finally {
      submit.disabled = false;
    }
  });
}
