"""공개 의존성 준비 뒤 격리된 Linux Docker에서 실제 모델을 검증한다. 외부 게시 없음."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]


def run(args):
    subprocess.run(args, check=True)


def output(args):
    return subprocess.check_output(args, text=True).strip()


def wheel_lock(folder):
    lines, seen = [], set()
    for path in sorted(folder.glob('*.whl')):
        with ZipFile(path) as archive:
            metadata = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata) != 1: raise ValueError('wheel metadata가 모호합니다.')
            parsed = BytesParser().parsebytes(archive.read(metadata[0]))
        name, version = parsed['Name'], parsed['Version']
        normalized = name.lower().replace('_', '-').replace('.', '-')
        if normalized in seen: raise ValueError('중복 wheel입니다.')
        seen.add(normalized)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        lines.append(f'{name}=={version} --hash=sha256:{digest}')
    if not lines: raise ValueError('wheel이 없습니다.')
    (folder / 'requirements.lock').write_text('\n'.join(lines) + '\n')


def download_model(folder):
    lock = json.loads((ROOT / 'deploy/worker/model-lock.json').read_text())
    target = folder / lock['revision']
    target.mkdir(parents=True)
    for item in lock['files']:
        path = target / item['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{lock['model_id']}/resolve/{lock['revision']}/{item['path']}"
        digest, size = hashlib.sha256(), 0
        with urlopen(url, timeout=90) as response, path.open('wb') as stream:
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > item['size']: raise ValueError('모델 크기 초과')
                digest.update(block)
                stream.write(block)
        if size != item['size'] or digest.hexdigest() != item['sha256']:
            raise ValueError('모델 해시 불일치')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() not in ('Linux', 'Darwin'):
        parser.error('Linux Docker 실행 환경이 필요합니다.')
    root = args.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    wheels, reports = root / 'wheels', root / 'reports'
    wheels.mkdir()
    reports.mkdir(mode=0o777)
    reports.chmod(0o777)
    run(['docker', 'pull', '--platform', 'linux/amd64', 'python:3.12-slim-bookworm'])
    base = output(['docker', 'inspect', '--format', '{{index .RepoDigests 0}}', 'python:3.12-slim-bookworm'])
    # 호스트가 Mac이어도 wheel 해석은 실제 Linux amd64 Python 안에서 한다.
    pip = ['docker', 'run', '--rm', '--platform', 'linux/amd64', '-v', f'{wheels}:/wheels',
           base, 'python', '-m', 'pip', 'download', '--only-binary=:all:', '--dest', '/wheels']
    run([*pip, '--no-deps', '--index-url', 'https://download.pytorch.org/whl/cpu', 'torch==2.14.0+cpu'])
    run([*pip, '--index-url', 'https://pypi.org/simple', '--find-links', '/wheels', 'torch==2.14.0+cpu',
         'sentence-transformers==6.1.0', 'transformers==5.17.0', 'pdfplumber==0.11.10',
         'requests==2.34.2', 'psycopg[binary]==3.3.9', 'psycopg-pool==3.3.3', 'boto3==1.44.4'])
    wheel_lock(wheels)
    shutil.copyfile(wheels / 'requirements.lock', reports / 'requirements.lock')
    model = download_model(root / 'models')
    run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--load',
         '--build-arg', f'PYTHON_RUNTIME_BASE={base}', '--build-context', f'worker_dependencies={wheels}',
         '-f', str(ROOT / 'deploy/worker/runtime-image/Dockerfile'), '-t', 'localhost:5000/slp-runtime:validation', str(ROOT)])
    # digest 확보용 registry는 이 CI 호스트의 loopback에서만 열고 종료 시 삭제한다.
    run(['docker', 'run', '-d', '--name', 'slp-registry', '-p', '127.0.0.1:5000:5000', 'registry:2'])
    try:
        run(['docker', 'push', 'localhost:5000/slp-runtime:validation'])
        runtime = output(['docker', 'inspect', '--format', '{{index .RepoDigests 0}}', 'localhost:5000/slp-runtime:validation'])
        manifest = output(['docker', 'run', '--rm', '--platform', 'linux/amd64', '--network=none', '--read-only',
                           runtime, '--image-reference', runtime])
        (reports / 'runtime-lock.json').write_text(manifest + '\n')
        run([sys.executable, str(ROOT / 'deploy/worker/build_render.py'), '--runtime-image', runtime,
             '--model-dir', str(model), '--runtime-dir', str(reports), '--execute'])
        image = 'solo-leveling-worker-render:validation'
        assert output(['docker', 'inspect', '--format', '{{.Architecture}}', image]) == 'amd64'
        verification = root / 'verification'
        shutil.copytree(ROOT / 'tests/fixtures', verification / 'tests/fixtures')
        (verification / 'tests/__init__.py').write_text('')
        shutil.copyfile(ROOT / 'evaluation/embedding/linux_worker_smoke.py', verification / 'smoke.py')
        limits = ['--platform', 'linux/amd64', '--network=none', '--read-only', '--cpus=2', '--memory=4g', '--memory-swap=4g',
                  '--pids-limit=256', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,size=256m']
        run(['docker', 'run', '--rm', *limits, '-e', 'PYTHONPATH=/app/src:/verification',
             '-v', f'{verification}:/verification:ro', '-v', f'{reports}:/results', '--entrypoint', 'python',
             image, '/verification/smoke.py', '--model-dir', f'/opt/models/{model.name}',
             '--output', '/results/linux-result.json'])
        run(['docker', 'run', '-d', '--name', 'slp-render-check', *limits, image])
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if 'render_standby' in output(['docker', 'logs', 'slp-render-check']): break
                time.sleep(1)
            else: raise RuntimeError('Render 대기 진입 실패')
            run(['docker', 'stop', '--time', '12', 'slp-render-check'])
            assert output(['docker', 'inspect', '--format', '{{.State.ExitCode}}', 'slp-render-check']) == '0'
            logs = output(['docker', 'logs', 'slp-render-check'])
            assert 'render_stopped' in logs and 'render_verification_started' not in logs
            (reports / 'render-standby.txt').write_text(logs + '\n')
        finally:
            run(['docker', 'rm', '-f', 'slp-render-check'])
        (reports / 'images.json').write_text(json.dumps(dict(python_base=base, runtime=runtime,
            final_image_id=output(['docker', 'inspect', '--format', '{{.Id}}', image]),
            platform='linux/amd64', external_registry_push=False, render_deployed=False), indent=2))
        print((reports / 'linux-result.json').read_text())
    finally:
        run(['docker', 'rm', '-f', 'slp-registry'])


if __name__ == '__main__':
    main()
