"""승인된 오프라인 wheel 입력과 Linux 설치 metadata를 확인한다."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys

from solo_leveling.workers.image_preflight import REQUIRED_PACKAGES


def require(ok):
    if not ok: raise ValueError('WORKER_RUNTIME_INVALID')


def canonical(name):
    require(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name))
    return re.sub(r'[-_.]+', '-', name).lower()


def image_reference(value):
    require(type(value) is str and re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', value))
    return value


def validate_inputs(folder):
    root = Path(folder)
    require(root.is_dir() and not root.is_symlink())
    files = list(root.iterdir())
    require(all(p.is_file() and not p.is_symlink() and (p.name == 'requirements.lock' or p.suffix == '.whl') for p in files))
    require(any(p.suffix == '.whl' for p in files))
    lock = root / 'requirements.lock'
    require(lock.is_file() and lock.stat().st_size <= 1_048_576)
    text = lock.read_text(encoding='utf-8').replace('\\\n', ' ')
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9.+!_-]+(?:\s+--hash=sha256:[0-9a-f]{64})+', line)
        require(match is not None)
        name = canonical(match.group(1))
        require(name not in names)
        names.add(name)
    require(REQUIRED_PACKAGES <= names)
    # 실제 wheel 내용 hash·모든 전이 의존성 pin·플랫폼 호환은 pip의 오프라인 설치에서 재검사한다.
    return True


def capture(reference, *, distributions=None):
    reference = image_reference(reference)
    require(platform.system() == 'Linux')
    packages = {}
    for dist in metadata.distributions() if distributions is None else distributions:
        name = canonical(dist.metadata['Name'])
        value = dist.version
        require(name not in packages and type(value) is str and bool(value.strip()))
        packages[name] = value
    require(REQUIRED_PACKAGES <= packages.keys())
    return dict(schema_version=1, base_image=reference, system=platform.system(), machine=platform.machine(),
                python=platform.python_version(), packages=dict(sorted(packages.items())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-reference', required=True)
    parser.add_argument('--validate-inputs')
    args = parser.parse_args(argv)
    try:
        image_reference(args.image_reference)
        if args.validate_inputs:
            validate_inputs(args.validate_inputs)
            print('WORKER_RUNTIME_INPUTS_VALID')
        else:
            print(json.dumps(capture(args.image_reference), sort_keys=True))
    except Exception:
        print('WORKER_RUNTIME_INVALID', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
