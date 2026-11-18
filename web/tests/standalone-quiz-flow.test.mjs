import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// 채점 스크립트만 실행하는 DOM 대역이다. 실제 배치·키보드 동작은 브라우저 검증 대상이다.
class Element {
  constructor(tag) {
    Object.assign(this, { tag, children: [], handlers: {}, className: '', text: '', style: {} });
    this.classList = {
      add: value => { this.className += ' ' + value; },
      remove: value => { this.className = this.className.split(' ').filter(x => x !== value).join(' '); },
    };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(x => x.textContent).join(''); }
  appendChild(node) { this.children.push(node); return node; }
  insertBefore(node, before) { assert(this.children.includes(before)); this.children.splice(this.children.indexOf(before), 0, node); }
  setAttribute() {}
  focus() {}
  addEventListener(name, handler) { this.handlers[name] = handler; }
  descendants() { return this.children.flatMap(x => [x, ...x.descendants()]); }
  querySelector(selector) {
    return this.descendants().find(x => selector.startsWith('.')
      ? x.className.split(' ').includes(selector.slice(1)) : x.tag === selector);
  }
  click() { assert(!this.disabled, '비활성 버튼 클릭 금지'); this.handlers.click(); }
}

function setup() {
  const root = new URL('../../skills/', import.meta.url);
  const quiz = JSON.parse(readFileSync(new URL('samples/quiz-attention.json', root), 'utf8'));
  // 객관식 두 개와 주관식 하나로 정답·오답·부분 점수를 함께 검증한다.
  quiz.questions = quiz.questions.slice(0, 3);
  quiz.questions.forEach((q, i) => { q.level = ['정의', '관계', '적용'][i]; });
  const template = readFileSync(new URL('solo-leveling-quiz/assets/quiz_template.html', root), 'utf8');
  const nodes = Object.fromEntries(['quiz-data', 'stage', 'title', 'paper', 'note', 'count', 'fill'].map(id => [id, new Element('div')]));
  nodes['quiz-data'].textContent = JSON.stringify(quiz);
  vm.runInNewContext(template.match(/<script>([\s\S]*?)<\/script>/)[1], {
    document: {
      getElementById: id => nodes[id], createElement: tag => new Element(tag),
      createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; },
    }, window: { scrollTo() {} },
  });
  const button = label => nodes.stage.descendants().find(x => x.tag === 'button' && x.textContent === label);
  const choose = index => nodes.stage.descendants().filter(x => x.className.split(' ').includes('choice'))[index].click();
  const next = () => (button('다음 문항 →') || button('결과 보기')).click();
  return { quiz, nodes, button, choose, next };
}

test('선택 전 채점 금지, 객관식 정답과 오답·근거 표시', () => {
  for (const correct of [true, false]) {
    const { quiz, nodes, button, choose } = setup();
    assert.equal(button('정답 확인').disabled, true);
    const q = quiz.questions[0];
    assert(!nodes.stage.textContent.includes(q.explanation));
    choose(correct ? q.correct_index : (q.correct_index + 1) % q.choices.length);
    button('정답 확인').click();
    assert(nodes.stage.textContent.includes(correct ? '정답이에요!' : '아쉬워요, 오답이에요.'));
    assert(nodes.stage.textContent.includes(q.explanation));
    assert(nodes.stage.textContent.includes(q.evidence[0].quote));
    assert.equal(nodes.stage.querySelector('.right').disabled, true);
    assert.equal(button('정답 확인'), undefined);
  }
});

test('주관식 빈 답 방지·자기 채점 세 가지·수준별 합계', () => {
  for (const [label, points] of [['맞았어요', 1], ['부분 정답', .5], ['틀렸어요', 0]]) {
    const { quiz, nodes, button, choose, next } = setup();
    choose(quiz.questions[0].correct_index); button('정답 확인').click(); next();
    const input = nodes.stage.querySelector('textarea');
    input.value = '   '; input.handlers.input(); assert.equal(button('정답 확인').disabled, true);
    input.value = '같은 시퀀스'; input.handlers.input(); button('정답 확인').click();
    assert.equal(input.readOnly, true);
    assert(nodes.stage.textContent.includes(quiz.questions[1].grading_criteria));
    assert.equal(button('다음 문항 →'), undefined);
    button(label).click(); next();
    choose((quiz.questions[2].correct_index + 1) % 4); button('정답 확인').click(); next();
    assert.equal(nodes.stage.querySelector('.score').textContent, `${1 + points} / 3`);
    const rows = nodes.stage.querySelector('table').children.slice(1).map(row => row.textContent);
    assert.deepEqual(rows, ['정의1개1 / 1', `관계1개${points} / 1`, '적용1개0 / 1']);
    assert.equal(nodes.count.textContent, '3 / 3');
  }
});

test('부분 정답과 오답만 재풀이하고 새 점수 반영·처음부터 초기화', () => {
  const { quiz, nodes, button, choose, next } = setup();
  choose(0); button('정답 확인').click(); next();
  const input = nodes.stage.querySelector('textarea'); input.value = '답'; input.handlers.input();
  button('정답 확인').click(); button('부분 정답').click(); next();
  choose(0); button('정답 확인').click(); next();
  button('틀린 문항만 다시 풀기').click();
  assert.equal(nodes.count.textContent, '0 / 2');
  assert(nodes.stage.textContent.includes(quiz.questions[1].prompt));
  const retryInput = nodes.stage.querySelector('textarea'); retryInput.value = '수정 답'; retryInput.handlers.input();
  button('정답 확인').click(); button('맞았어요').click(); next();
  choose(quiz.questions[2].correct_index); button('정답 확인').click(); next();
  assert.equal(nodes.stage.querySelector('.score').textContent, '2 / 2');
  assert.equal(button('틀린 문항만 다시 풀기'), undefined);
  button('처음부터 다시').click();
  assert.equal(nodes.count.textContent, '0 / 3');
  assert.equal(nodes.fill.style.width, '0%');
  assert.equal(button('정답 확인').disabled, true);
});
