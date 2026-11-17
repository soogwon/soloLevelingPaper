"""준비된 입력으로 Render linux/amd64 이미지를 로컬 빌드한다. 게시·배포하지 않는다."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def commands(runtime_image, model, runtime, tag, repo):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', runtime_image):
        raise ValueError('고정 runtime 이미지 digest가 필요합니다.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.:/-]*', tag):
        raise ValueError('로컬 이미지 이름이 올바르지 않습니다.')
    model, runtime = Path(model).resolve(), Path(runtime).resolve()
    if not model.is_dir() or not (runtime / 'runtime-lock.json').is_file():
        raise ValueError('모델과 runtime-lock 입력이 필요합니다.')
    return [
        ['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--load', '--target', 'render',
         '--build-arg', f'WORKER_RUNTIME_IMAGE={runtime_image}',
         '--build-context', f'worker_model={model}', '--build-context', f'worker_runtime={runtime}',
         '-f', str(repo / 'deploy/worker/Dockerfile'), '-t', tag, str(repo)],
        ['docker', 'run', '--rm', '--network=none', '--read-only', '--platform', 'linux/amd64',
         '--entrypoint', 'python', tag, '/opt/worker/image_preflight.py'],
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-image', required=True)
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--tag', default='solo-leveling-worker-render:validation')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    plan = commands(args.runtime_image, args.model_dir, args.runtime_dir, args.tag,
                    Path(__file__).resolve().parents[2])
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    for command in plan: subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
