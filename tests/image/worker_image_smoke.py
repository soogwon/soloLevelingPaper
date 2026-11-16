"""Linux Docker에서 합성 artifact 대상만 검증한다. 배포·추론·API 호출은 없다."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from tests.unit.test_worker_image import synthetic_bundle


def run(args, *, success=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if success:
        if result.returncode:
            raise RuntimeError(result.stdout)
    else:
        assert result.returncode != 0 and 'WORKER_IMAGE_INPUT_INVALID' in result.stdout, result.stdout
    return result.stdout


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    context = root / 'context'
    shutil.copytree('src', context / 'src')
    shutil.copytree('deploy/worker', context / 'deploy/worker')
    model, lock = synthetic_bundle(root)
    revision = json.loads(Path('deploy/worker/model-lock.json').read_text())['revision']
    model = model.rename(root / revision)
    lock['revision'] = revision
    (context / 'deploy/worker/model-lock.json').write_text(json.dumps(lock))
    runtime = root / 'runtime'
    runtime.mkdir()
    (runtime / 'runtime-lock.json').write_text('{}')
    # 이 base는 합성 artifact 검사 전용이며 운영 runtime 승인값이 아니다.
    run(['docker', 'pull', 'python:3.12-slim'])
    base = run(['docker', 'inspect', '--format', '{{index .RepoDigests 0}}', 'python:3.12-slim']).strip()
    build = ['docker', 'buildx', 'build', '--load', '--build-arg', f'WORKER_RUNTIME_IMAGE={base}',
        '--build-context', f'worker_model={model}', '--build-context', f'worker_runtime={runtime}',
        '-f', str(context / 'deploy/worker/Dockerfile'), '-t', 'slp-worker-contract', str(context)]
    run([*build, '--target', 'artifact'])
    output = run(['docker', 'run', '--rm', '--network=none', '--read-only', 'slp-worker-contract'])
    assert output.strip() == 'WORKER_ARTIFACT_VERIFIED'
    script = '''import os
from pathlib import Path
assert os.getuid() != 0
assert not Path('/app/src').exists()
for root in ('/opt/models', '/opt/worker'):
    for path in (Path(root), *Path(root).rglob('*')):
        assert path.stat().st_mode & 0o222 == 0
try:
    Path('/opt/worker/model-lock.json').write_text('changed')
except PermissionError:
    pass
else:
    raise AssertionError('쓰기 허용')
'''
    # read-only mount 없이도 non-root가 모델/manifest를 변경할 수 없어야 한다.
    run(['docker', 'run', '--rm', '--network=none', '--entrypoint', 'python',
         'slp-worker-contract', '-c', script])
    run([*build, '--target', 'worker'], success=False)
    (model / 'config.json').write_bytes(b'[]')
    run([*build, '--target', 'artifact'], success=False)
    print('합성 이미지: 정상/비루트/읽기 전용/누락 runtime/변조 거부 통과')
