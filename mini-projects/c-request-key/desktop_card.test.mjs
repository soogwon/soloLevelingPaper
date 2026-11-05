import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = (await readFile(new URL('desktop_card.js', import.meta.url), 'utf8'))
  .replace("import { App } from '@modelcontextprotocol/ext-apps';", '');

for (const failure of ['mcp-error', 'rejected-call']) {
  test(failure + ' hides error text and never submits while restoring', async () => {
    const nodes = { output: {}, '#submit': {}, '#restore': {} };
    const calls = [];
    let fail = true;
    class App {
      async connect() {}
      async callServerTool(request) {
        calls.push(request);
        if (fail) {
          if (failure === 'rejected-call') throw new Error('PRIVATE_ERROR');
          return { isError: true, content: [{ type: 'text', text: 'PRIVATE_ERROR' }] };
        }
        return { content: [{ type: 'text', text: '저장된 결과 복원' }] };
      }
    }
    await vm.runInNewContext('(async () => {' + source + '})()', {
      App, document: { querySelector: selector => nodes[selector] },
    });
    await nodes['#restore'].onclick();
    assert.equal(nodes.output.textContent, '상태 확인 불가');
    fail = false;
    await nodes['#restore'].onclick();
    assert.equal(nodes.output.textContent, '저장된 결과 복원');
    assert.deepEqual(calls.map(call => call.arguments.action), ['restore', 'restore']);
  });
}
