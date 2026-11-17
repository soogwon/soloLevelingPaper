"""합성 wheel로만 오프라인 runtime Docker 빌드·해시 거부를 확인한다."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile

from solo_leveling.workers.image_preflight import REQUIRED_PACKAGES


def run(args, success=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert (result.returncode == 0) is success, result.stdout
    return result.stdout


with tempfile.TemporaryDirectory() as raw:
    bundle = Path(raw)
    lines = []
    for name in sorted(REQUIRED_PACKAGES):
        normalized = name.replace('-', '_')
        path = bundle / f'{normalized}-1.0-py3-none-any.whl'
        metadata = f'{normalized}-1.0.dist-info'
        with ZipFile(path, 'w') as wheel:
            wheel.writestr(metadata + '/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n')
            wheel.writestr(metadata + '/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            wheel.writestr(metadata + '/RECORD', '')
        lines.append(f'{name}==1.0 --hash=sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}')
    (bundle / 'requirements.lock').write_text('\n'.join(lines))
    # 합성 검사 전용 base이며 운영 runtime 승인값으로 사용하지 않는다.
    run(['docker', 'pull', 'python:3.12-slim'])
    base = run(['docker', 'inspect', '--format', '{{index .RepoDigests 0}}', 'python:3.12-slim']).strip()
    command = ['docker', 'buildx', 'build', '--load', '--build-arg', f'PYTHON_RUNTIME_BASE={base}',
        '--build-context', f'worker_dependencies={bundle}', '-f', 'deploy/worker/runtime-image/Dockerfile',
        '-t', 'slp-synthetic-runtime', '.']
    run(command)
    output = run(['docker', 'run', '--rm', '--network=none', '--read-only', 'slp-synthetic-runtime',
                  '--image-reference', base])
    manifest = json.loads(output)
    assert manifest['system'] == 'Linux'
    assert all(manifest['packages'][name] == '1.0' for name in REQUIRED_PACKAGES)
    run(['docker', 'run', '--rm', '--network=none', '--read-only', '--entrypoint', 'python',
         'slp-synthetic-runtime', '-c', 'import os; from pathlib import Path; assert os.getuid()!=0; assert not Path("/runtime-input").exists()'])
    path = next(bundle.glob('*.whl'))
    path.write_bytes(path.read_bytes() + b'changed')
    output = run(command, success=False)
    assert 'HASHES' in output.upper() or 'HASH' in output.upper()
    print('합성 runtime: 오프라인 설치/metadata/비루트/입력 미포함/변조 거부 통과')
