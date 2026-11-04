import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// 저장소의 상태 카드가 사용하는 SDK와 빌드 도구를 그대로 쓴다.
const ui = fileURLToPath(new URL('../../ui/paper-status/', import.meta.url));
const require = createRequire(path.join(ui, 'package.json'));
const { build } = require('esbuild');
const bundle = await build({
  stdin: { contents: await readFile(new URL('desktop_card.js', import.meta.url), 'utf8'), resolveDir: ui },
  bundle: true, write: false, format: 'esm', minify: true
});
const script = bundle.outputFiles[0].text.replace(/<\/script/gi, '<\\/script');
await writeFile(path.join(process.argv[2], 'card.html'),
  '<meta charset="utf-8"><button id="submit">합성 요청 1회 제출</button> ' +
  '<button id="restore">저장된 요청 조회</button> <output></output>' +
  '<script type="module">' + script + '</script>');
