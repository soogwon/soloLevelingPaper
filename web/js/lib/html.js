// 사용자 데이터(메모 본문, 논문 제목 등)는 항상 이스케이프해서 넣는다.
// html`...` 태그 안의 ${값}은 자동으로 이스케이프되고, raw()로 감싼 값과 중첩된 html`` 결과만 그대로 들어간다.

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

class SafeHtml {
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

export function raw(value) {
  return new SafeHtml(String(value));
}

function renderValue(value) {
  if (value === null || value === undefined || value === false) return '';
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(renderValue).join('');
  return esc(value);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, i) => {
    out += renderValue(value) + strings[i + 1];
  });
  return new SafeHtml(out);
}

export function setHTML(element, content) {
  // 배열은 html`` 안과 같이 항목별로 처리한다. 쉼표로 이어 붙은 이스케이프 문자열이 되지 않게 한다.
  element.innerHTML = renderValue(content);
}
