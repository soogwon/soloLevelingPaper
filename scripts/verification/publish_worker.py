"""승인한 GHCR private 두 이미지에만 게시하고 digest 기준으로 검증한다."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.verification.linux_worker import ROOT, run, output, download_model, verify_image

OWNER = 'soogwon'
NAMES = ('solo-leveling-worker-runtime', 'solo-leveling-worker')
BASE = 'python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df'


def private_package(name):
    if name not in NAMES: raise ValueError('게시 대상 불일치')
    request = Request(f'https://api.github.com/users/{OWNER}/packages/container/{name}',
        headers={'Authorization': f"Bearer {os.environ['GH_TOKEN']}", 'Accept': 'application/vnd.github+json'})
    try:
        with urlopen(request, timeout=30) as response: package = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            raise RuntimeError('미리 생성하고 private으로 확인한 package가 필요합니다.') from None
        raise RuntimeError('비공개 package 조회 실패') from None
    if package.get('visibility') != 'private':
        raise RuntimeError('기존 package가 private이 아니므로 게시 중단')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if not args.publish or not re.fullmatch(r'verified-[0-9a-f]{40}', args.tag):
        parser.error('명시적 게시 옵션과 검증 commit tag가 필요합니다.')
    for name in NAMES: private_package(name)
    root = args.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    wheels, reports = root / 'wheels', root / 'reports'
    wheels.mkdir()
    reports.mkdir(mode=0o777)
    reports.chmod(0o777)
    lock = ROOT / 'deploy/worker/linux-amd64-requirements.lock'
    run(['docker', 'pull', '--platform', 'linux/amd64', BASE])
    run(['docker', 'run', '--rm', '--platform', 'linux/amd64', '-v', f'{wheels}:/wheels',
         '-v', f'{lock}:/requirements.lock:ro', BASE, 'python', '-m', 'pip', 'download',
         '--require-hashes', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
         '--extra-index-url', 'https://download.pytorch.org/whl/cpu', '--dest', '/wheels', '-r', '/requirements.lock'])
    shutil.copyfile(lock, wheels / 'requirements.lock')
    shutil.copyfile(lock, reports / 'requirements.lock')
    model = download_model(root / 'models')
    runtime_tag, worker_tag = [f'ghcr.io/{OWNER}/{name}:{args.tag}' for name in NAMES]
    run(['docker', 'buildx', 'build', '--load', '--platform', 'linux/amd64',
         '--label', 'org.opencontainers.image.source=https://github.com/soogwon/soloLevelingPaper',
         '--build-arg', f'PYTHON_RUNTIME_BASE={BASE}', '--build-context', f'worker_dependencies={wheels}',
         '-f', str(ROOT / 'deploy/worker/runtime-image/Dockerfile'), '-t', runtime_tag, str(ROOT)])
    private_package(NAMES[0])
    run(['docker', 'push', runtime_tag])
    private_package(NAMES[0])
    runtime = output(['docker', 'inspect', '--format', '{{index .RepoDigests 0}}', runtime_tag])
    manifest = output(['docker', 'run', '--rm', '--network=none', '--read-only', runtime, '--image-reference', runtime])
    (reports / 'runtime-lock.json').write_text(manifest + '\n')
    run([sys.executable, str(ROOT / 'deploy/worker/build_render.py'), '--runtime-image', runtime,
         '--model-dir', str(model), '--runtime-dir', str(reports), '--tag', worker_tag, '--execute'])
    verify_image(root, model, worker_tag)
    private_package(NAMES[1])
    run(['docker', 'push', worker_tag])
    private_package(NAMES[1])
    worker = output(['docker', 'inspect', '--format', '{{index .RepoDigests 0}}', worker_tag])
    run(['docker', 'pull', worker])
    # 게시 digest에서 다시 검증하여 tag 변경이나 다른 image ID를 성공으로 오인하지 않는다.
    shutil.rmtree(root / 'verification')
    verify_image(root, model, worker)
    (reports / 'published.json').write_text(json.dumps(dict(runtime=runtime, worker=worker,
        visibility='private', tag=args.tag, render_deployed=False, api_calls=0), indent=2))
    print((reports / 'published.json').read_text())


if __name__ == '__main__':
    main()
