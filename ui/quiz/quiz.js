function payload(result) {
  if (result?.isError) throw new Error('도구 오류');
  if (result?.structuredContent) return result.structuredContent;
  return JSON.parse(result.content.find(item => item.type === 'text').text);
}

export function mountQuiz(app, doc) {
  const content = doc.getElementById('content');
  const notice = doc.getElementById('notice');
  let view = null;

  function element(tag, text) {
    const node = doc.createElement(tag);
    if (text != null) node.textContent = text;
    return node;
  }

  function showAttempt(attempt) {
    view = attempt;
    content.replaceChildren();
    notice.textContent = '채점 결과: ' + attempt.correct_count + ' / ' + attempt.total_count;
    for (const result of attempt.results) {
      const row = element('article');
      const submitted = result.selected_index != null ? result.choices[result.selected_index] : result.submitted_text;
      row.append(element('h2', result.prompt), element('p', result.is_correct ? '정답' : '오답'),
        element('p', '내 답: ' + (submitted || '무응답')), element('p', '정답: ' + result.correct_answer));
      if (result.grading_criteria) row.append(element('p', '채점 기준: ' + result.grading_criteria));
      content.append(row);
    }
    if (attempt.correct_count === attempt.total_count) return;
    const review = element('button', '오답 원문 근거 보기');
    const evidence = element('section');
    content.append(review, evidence);
    review.onclick = async () => {
      review.disabled = true;
      try {
        const data = payload(await app.callServerTool({name: 'review_quiz_attempt',
          arguments: {context_id: attempt.context_id, attempt_id: attempt.attempt_id}}));
        if (view !== attempt) return;
        if (data.context_id !== attempt.context_id || data.attempt_id !== attempt.attempt_id) throw new Error();
        const rows = [];
        for (const item of data.items) {
          rows.push(element('h3', item.result.prompt));
          for (const source of item.evidence) {
            rows.push(element('p', 'PDF ' + source.pdf_page + '페이지'), element('blockquote', source.quote_original));
          }
        }
        evidence.replaceChildren(...rows);
      } catch {
        if (view === attempt) evidence.textContent = '오답 근거를 불러오지 못했습니다.';
      } finally { review.disabled = false; }
    };
  }

  function showQuiz(quiz) {
    view = quiz;
    content.replaceChildren();
    notice.textContent = '답할 문항을 선택하세요. 단답형은 정규화 후 완전 일치로 채점합니다.';
    const fields = [];
    for (const question of quiz.questions) {
      const box = element('fieldset');
      box.append(element('legend', question.prompt));
      const index = question.question_index;
      if (question.question_type === 'multiple_choice') {
        question.choices.forEach((choice, i) => {
          const label = element('label');
          const input = element('input');
          input.type = 'radio'; input.name = 'q-' + index; input.value = String(i);
          label.append(input, doc.createTextNode(choice)); box.append(label);
        });
        fields.push(() => {
          const selected = box.querySelector('input:checked');
          return selected && {question_index: index, selected_index: Number(selected.value)};
        });
      } else {
        const input = element('textarea');
        input.setAttribute('aria-label', question.prompt);
        box.append(input);
        fields.push(() => input.value && {question_index: index, text: input.value});
      }
      content.append(box);
    }
    const submit = element('button', '답안 제출');
    content.append(submit);
    let request;
    submit.onclick = async () => {
      if (!request) {
        const answers = fields.map(read => read()).filter(Boolean);
        if (!answers.length) { notice.textContent = '한 문항 이상 답해주세요.'; return; }
        request = {context_id: quiz.context_id, step: quiz.step, quiz_id: quiz.quiz_id,
          request_key: doc.defaultView.crypto.randomUUID(), answers};
      }
      submit.disabled = true;
      for (const box of content.querySelectorAll('fieldset')) box.disabled = true;
      try {
        const response = await app.callServerTool({name: 'submit_quiz', arguments: request});
        if (view !== quiz) return;
        if (response.isError && response.content?.some(item => /QUIZ_CHANGED|REQUEST_CONFLICT|INVALID_ARGUMENT/.test(item.text))) {
          notice.textContent = '제출이 거부됐습니다. 채팅에서 퀴즈를 다시 열어 답안을 확인하세요.';
          return;
        }
        const attempt = payload(response);
        if (!Array.isArray(attempt.results) || attempt.context_id !== quiz.context_id ||
            attempt.step !== quiz.step || attempt.quiz_id !== quiz.quiz_id || !attempt.attempt_id) throw new Error();
        showAttempt(attempt);
      } catch {
        if (view !== quiz) return;
        notice.textContent = '제출 결과를 확인하지 못했습니다. 같은 답안으로 다시 확인할 수 있습니다.';
        submit.textContent = '같은 제출 다시 확인'; submit.disabled = false;
      }
    };
  }

  app.ontoolresult = result => {
    try {
      const data = payload(result);
      if (Array.isArray(data.results) && data.attempt_id) showAttempt(data);
      else if (Array.isArray(data.questions) && data.quiz_id) {
        if (view?.context_id === data.context_id && view?.quiz_id === data.quiz_id && view?.step === data.step) return;
        showQuiz(data);
      } else throw new Error();
    } catch {
      view = null; content.replaceChildren(); notice.textContent = '퀴즈를 표시할 수 없습니다. 채팅의 도구 응답을 확인하세요.';
    }
  };
  app.connect().catch(() => { notice.textContent = '카드 연결에 실패했습니다. 채팅의 도구 응답을 확인하세요.'; });
}
