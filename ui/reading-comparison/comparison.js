import { validSession } from '../reading-before/reading.js';

const groups = [
  ['hypothesis_vs_paper', '가설과 논문의 설명', 'before.hypothesis', ['after.claim', 'after.evidence']],
  ['background_vs_limitation', '배경지식과 적용 한계', 'before.background', ['after.limitation']],
  ['expectation_vs_application', '기대와 실제 적용', 'before.expectation', ['after.application']],
];
const states = { draft: '작성 중', completed: '작성 완료' };
const text = value => typeof value === 'string' && value.length > 0;
const templateFields = ['template_id', 'phase', 'category', 'prompt', 'step'];
const answerFields = ['context_id', 'phase', 'template_id', 'text', 'status', 'created_at', 'updated_at'];
function sameItem(a, b) {
  return a && b && templateFields.every(k => a.template?.[k] === b.template?.[k])
    && (a.answer === null ? b.answer === null
      : a.answer && b.answer && answerFields.every(k => a.answer[k] === b.answer[k]));
}
function valid(data, context) {
  if (!validSession(data?.before, context, 'before') || !validSession(data?.after, context, 'after')
      || data.context_id !== context || !Array.isArray(data.groups) || data.groups.length !== groups.length
      || data.before.total !== 3 || data.after.total !== 4) return false;
  const items = [...data.before.items, ...data.after.items];
  if (!items.every(i => templateFields.every(k => text(i.template[k]))
      && (i.answer === null || (text(i.answer.created_at) && text(i.answer.updated_at))))) return false;
  return groups.every(([id, , before, after], n) => {
    const g = data.groups[n];
    return g?.group_id === id && g.before?.template?.template_id === before
      && sameItem(g.before, items.find(i => i.template.template_id === before))
      && Array.isArray(g.after) && g.after.length === after.length
      && after.every((key, pos) => g.after[pos]?.template?.template_id === key
        && sameItem(g.after[pos], items.find(i => i.template.template_id === key)));
  });
}

export function mountComparison(app, doc, timeoutMs = 15000) {
  const el = id => doc.getElementById(id);
  let context = null, connected = false, busy = false, generation = 0;
  function notice(message) {
    el('notice').textContent = message;
    el('refresh').disabled = !connected || !context || busy;
  }
  function clear() { el('groups').replaceChildren(); el('counts').textContent = ''; }
  function display(result) {
    clear();
    if (result?.isError || !valid(result?.structuredContent, context)) {
      notice('비교 결과를 확인하지 못했습니다. 다시 조회하거나 채팅의 응답을 확인하세요.'); return;
    }
    const data = result.structuredContent;
    el('counts').textContent = ['before', 'after'].map(phase => {
      const s = data[phase];
      return `${phase === 'before' ? '읽기 전' : '읽기 후'}: 작성 ${s.written}/${s.total} · 완료 ${s.completed}/${s.total}`;
    }).join(' / ');
    function itemView(item) {
      const box = doc.createElement('article');
      const heading = doc.createElement('h3'); heading.textContent = item.template.prompt;
      const status = doc.createElement('p');
      status.textContent = item.answer === null ? '미작성' : states[item.answer.status];
      const body = doc.createElement('pre'); body.textContent = item.answer?.text ?? '';
      const modified = doc.createElement('small');
      modified.textContent = item.answer ? `마지막 수정: ${item.answer.updated_at}` : '';
      box.append(heading, status, body, modified); return box;
    }
    data.groups.forEach((group, i) => {
      const section = doc.createElement('section');
      const heading = doc.createElement('h2'); heading.textContent = groups[i][1];
      const columns = doc.createElement('div'); columns.className = 'columns';
      for (const [label, items] of [['읽기 전', [group.before]], ['읽기 후', group.after]]) {
        const column = doc.createElement('div');
        const title = doc.createElement('h3'); title.textContent = label;
        column.append(title, ...items.map(itemView)); columns.append(column);
      }
      section.append(heading, columns); el('groups').append(section);
    });
    notice('현재 저장된 답변입니다. 수정 이력이나 이해도 변화 판정이 아닙니다.');
  }
  app.ontoolinput = ({arguments: args}) => {
    generation++; context = text(args?.context_id) && args.context_id.trim() ? args.context_id : null;
    busy = false; clear(); notice('읽기 전후 답변을 불러오고 있습니다.');
  };
  app.ontoolresult = result => { generation++; busy = false; display(result); };
  app.ontoolcancelled = () => { generation++; context = null; busy = false; clear(); notice('조회가 취소되었습니다.'); };
  el('refresh').onclick = async () => {
    if (!connected || !context || busy) return;
    const ticket = ++generation; busy = true; clear(); notice('비교 결과를 다시 조회하고 있습니다.');
    let timer;
    try {
      const result = await Promise.race([
        app.callServerTool({name: 'get_reading_comparison', arguments: {context_id: context}}),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error()), timeoutMs); }),
      ]);
      if (ticket === generation) { busy = false; display(result); }
    } catch {
      if (ticket === generation) { busy = false; clear(); notice('조회가 지연되거나 실패했습니다. 다시 조회해 주세요.'); }
    } finally { clearTimeout(timer); }
  };
  notice('호스트 연결을 기다리고 있습니다.');
  Promise.resolve().then(() => app.connect()).then(() => { connected = true; el('refresh').disabled = !context || busy; })
    .catch(() => { connected = false; clear(); notice('화면 연결에 실패했습니다. 채팅의 응답을 확인하세요.'); });
}
