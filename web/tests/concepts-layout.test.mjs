import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '' } };

const { layoutGraph, sourceLink } = await import('../js/pages/concepts.js');

const concept = (id) => ({ concept_id: id, name_ko: id, name_en: id });
const edge = (prerequisite_id, concept_id) => ({ prerequisite_id, concept_id });


test('선수 개념은 항상 그 개념보다 왼쪽 열에 놓인다', () => {
  const graph = {
    concepts: ['a', 'b', 'c', 'd'].map(concept),
    prerequisites: [edge('a', 'b'), edge('b', 'd'), edge('c', 'd'), edge('a', 'c')],
  };
  const { pos, width, height } = layoutGraph(graph);
  for (const e of graph.prerequisites) {
    assert.ok(pos.get(e.prerequisite_id).x < pos.get(e.concept_id).x);
  }
  assert.equal(pos.size, 4);
  assert.ok(width > 0 && height > 0);
});


test('같은 열의 노드는 겹치지 않는다', () => {
  const graph = { concepts: ['a', 'b', 'c'].map(concept), prerequisites: [] };
  const ys = [...layoutGraph(graph).pos.values()].map((p) => p.y).sort((x, y) => x - y);
  assert.equal(new Set(ys).size, 3);
});


test('원문 링크는 웹 주소만 연다', () => {
  assert.equal(sourceLink('https://arxiv.org/pdf/1706.03762v7'), 'https://arxiv.org/pdf/1706.03762v7');
  for (const value of [null, '/papers', 'about:blank', 'file:///tmp/paper.pdf']) {
    assert.equal(sourceLink(value), null);
  }
});


test('경로 보기는 단계와 건너뛴 개념, 그 사이 관계만 남긴다', async () => {
  const { focusGraph } = await import('../js/pages/concepts.js');
  const graph = {
    concepts: ['a', 'b', 'c', 'd'].map(concept),
    prerequisites: [edge('a', 'b'), edge('b', 'd'), edge('c', 'd')],
  };
  const path = { steps: [{ concept_id: 'b' }, { concept_id: 'd' }], skipped: [{ concept_id: 'a' }] };
  const shown = focusGraph(graph, path);
  assert.deepEqual(shown.concepts.map((c) => c.concept_id), ['a', 'b', 'd']);
  assert.deepEqual(shown.prerequisites, [edge('a', 'b'), edge('b', 'd')]);
  assert.equal(focusGraph(graph, null), graph);
  assert.equal(focusGraph(graph, { steps: [], skipped: [] }), graph);
});
