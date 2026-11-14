import assert from 'node:assert/strict';
import test from 'node:test';


globalThis.window = { location: { search: '' } };

const { layoutGraph } = await import('../js/pages/concepts.js');

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
