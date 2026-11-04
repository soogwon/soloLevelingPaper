import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { mountQuiz } from './quiz.js';

const quiz = {context_id:'ctx', step:'preview', quiz_id:'q', questions:[
  {question_index:0, question_type:'multiple_choice', prompt:'선택', choices:['갑','을']},
  {question_index:1, question_type:'short_answer', prompt:'설명', choices:[]}
]};
const attempt = {context_id:'ctx', step:'preview', quiz_id:'q', attempt_id:'a',
  correct_count:0, total_count:2, results:[{prompt:'선택', choices:['갑','을'],
    selected_index:0, correct_answer:'을', grading_criteria:'기준', is_correct:false}]};
const response = data => ({structuredContent:data});
function setup(call) {
  const doc = new JSDOM(readFileSync(new URL('./template.html', import.meta.url),'utf8')).window.document;
  const app = {connect: async () => {}, callServerTool:call};
  mountQuiz(app,doc);
  app.ontoolresult(response(quiz));
  return {doc,app};
}

test('응답 유실 후 같은 키와 원래 답안으로 제출하고 오답 원문을 조회한다', async () => {
  const calls = [];
  const {doc} = setup(async req => {
    calls.push(structuredClone(req));
    if (calls.length === 1) throw new Error();
    if (req.name === 'submit_quiz') return response(attempt);
    return response({context_id:'ctx', attempt_id:'a',items:[{result:attempt.results[0],
      evidence:[{pdf_page:2,quote_original:'<script>원문</script>'}]}]});
  });
  const submit = doc.querySelector('button');
  await submit.onclick();
  assert.equal(calls.length,0);
  doc.querySelector('input').checked = true;
  await submit.onclick();
  assert.equal(doc.querySelector('fieldset').disabled,true);
  await submit.onclick();
  assert.deepEqual(calls[0],calls[1]);
  assert.ok(calls[0].arguments.request_key);
  assert.deepEqual(calls[0].arguments.answers,[{question_index:0,selected_index:0}]);
  assert.match(doc.body.textContent,/채점 결과: 0 \/ 2/);
  await doc.querySelector('button').onclick();
  assert.deepEqual(calls[2],{name:'review_quiz_attempt',arguments:{context_id:'ctx',attempt_id:'a'}});
  assert.equal(doc.querySelector('blockquote').textContent,'<script>원문</script>');
  assert.equal(doc.querySelector('blockquote script'),null);
});

test('늦게 돌아온 결과가 새 퀴즈를 덮어쓰지 않는다', async () => {
  let resolve;
  const {doc,app} = setup(() => new Promise(done => {resolve=done;}));
  doc.querySelector('textarea').value='답';
  const pending=doc.querySelector('button').onclick();
  app.ontoolresult(response({...quiz,quiz_id:'next'}));
  resolve(response(attempt));
  await pending;
  assert.equal(doc.querySelector('button').textContent,'답안 제출');
  assert.equal(doc.querySelector('textarea').value,'');
});

test('퀴즈 교체 오류는 자동 재전송하지 않는다', async () => {
  let calls=0;
  const {doc} = setup(async () => {calls++;return {isError:true,content:[{text:'QUIZ_CHANGED'}]};});
  doc.querySelector('textarea').value='답';
  await doc.querySelector('button').onclick();
  assert.equal(calls,1);
  assert.equal(doc.querySelector('button').disabled,true);
  assert.match(doc.getElementById('notice').textContent,/다시 열어/);
});
