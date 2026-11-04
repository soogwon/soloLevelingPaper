import { App } from '@modelcontextprotocol/ext-apps';

const app = new App({ name: 'C 요청 복구 실험', version: '1' });
const status = document.querySelector('output');
async function act(action) {
  try {
    const result = await app.callServerTool({name: 'probe_action', arguments: {action}});
    status.textContent = result.content[0].text;
  } catch {
    status.textContent = '상태 확인 불가';
  }
}
document.querySelector('#submit').onclick = () => act('submit');
document.querySelector('#restore').onclick = () => act('restore');
app.ontoolresult = () => { status.textContent = '카드 준비'; };
await app.connect();
