import { build } from 'esbuild';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// 외부 CDN 없이 실행되도록 SDK와 화면 코드를 하나의 HTML로 묶는다.
const result = await build({
  entryPoints: [fileURLToPath(new URL('./main.js', import.meta.url))],
  bundle: true, write: false, format: 'iife', platform: 'browser',
  target: 'es2022', minify: true, legalComments: 'inline', metafile: true,
});
const template = await readFile(new URL('./template.html', import.meta.url), 'utf8');
const script = result.outputFiles[0].text.replace(/<\/script/gi, '<\\/script');
const html = template.replace('<!-- APP_SCRIPT -->', () => `<script>${script}</script>`);
const output = new URL('../../src/solo_leveling/interfaces/mcp/assets/', import.meta.url);
await mkdir(output, { recursive: true });
await writeFile(new URL('learning-dashboard.html', output), html, 'utf8');

// 번들에 실제 포함된 의존성의 라이선스를 배포 파일과 함께 보존한다.
const packages = new Set();
const notices = [];
for (const input of Object.keys(result.metafile.inputs)) {
  if (!input.includes('node_modules')) continue;
  let directory = path.dirname(path.resolve(input));
  while (directory !== path.dirname(directory)) {
    let info;
    try { info = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8')); }
    catch { directory = path.dirname(directory); continue; }
    if (!info.name) { directory = path.dirname(directory); continue; }
    if (!packages.has(directory)) {
      packages.add(directory);
      let license;
      for (const name of ['LICENSE', 'LICENSE.txt', 'LICENSE.md', 'license', 'license.md']) {
        try { license = await readFile(path.join(directory, name), 'utf8'); break; } catch {}
      }
      if (!license) throw new Error(`라이선스 파일 확인 필요: ${info.name}`);
      notices.push(`${info.name} ${info.version}\n${license.replace(/[ \t]+$/gm, '')}`);
    }
    break;
  }
}
await writeFile(new URL('learning-dashboard.LICENSE.txt', output), notices.join('\n\n---\n\n'), 'utf8');
