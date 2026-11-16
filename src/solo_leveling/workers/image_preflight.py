"""이미지 입력을 오프라인 검사한다. 모델·DB·제공자를 실행하지 않는다."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import sys

REQUIRED_PACKAGES = frozenset({'torch', 'sentence-transformers', 'transformers', 'tokenizers',
    'numpy', 'pdfplumber', 'requests', 'psycopg', 'psycopg-pool', 'boto3'})


def require(ok):
    if not ok:
        raise ValueError('WORKER_IMAGE_INPUT_INVALID')


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def artifact_check(folder, lock):
    require(type(lock) is dict and lock.get('schema_version') == 1)
    revision = lock.get('revision')
    require(type(revision) is str and re.fullmatch('[0-9a-f]{40}', revision))
    require(folder.name == revision and folder.is_dir() and not folder.is_symlink())
    entries = lock.get('files')
    require(type(entries) is list and bool(entries))
    expected, hashes = set(), []
    for entry in entries:
        require(type(entry) is dict)
        name, size, digest = entry.get('path'), entry.get('size'), entry.get('sha256')
        require(type(name) is str and name and '\\' not in name)
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and str(path) == name
                and name not in expected and type(size) is int and size >= 0
                and type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest))
        expected.add(name)
        target = folder.joinpath(*path.parts)
        for part in (target, *target.parents):
            if part == folder.parent:
                break
            require(not part.is_symlink())
        require(stat.S_ISREG(target.stat().st_mode) and target.stat().st_size == size)
        sha = hashlib.sha256()
        with target.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(block)
        require(sha.hexdigest() == digest)
        if target.suffix in ('.json', '.txt', '.model', '.safetensors', '.bin'):
            hashes.append((name, digest))
    actual = set()
    for path in folder.rglob('*'):
        require(not path.is_symlink())
        if path.is_dir():
            continue
        require(stat.S_ISREG(path.stat().st_mode))
        actual.add(path.relative_to(folder).as_posix())
    require(actual == expected)
    raw = json.dumps(sorted(hashes), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    require(hashlib.sha256(raw.encode()).hexdigest() == lock.get('artifact_sha256'))


def runtime_check(lock, base_ref, *, get_version=version):
    require(type(lock) is dict and lock.get('schema_version') == 1)
    require(type(base_ref) is str and re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', base_ref))
    require(lock.get('base_image') == base_ref and lock.get('system') == platform.system() == 'Linux'
            and lock.get('machine') == platform.machine() and lock.get('python') == platform.python_version())
    packages = lock.get('packages')
    require(type(packages) is dict and REQUIRED_PACKAGES <= packages.keys())
    for name, expected in packages.items():
        require(type(name) is str and type(expected) is str and expected and get_version(name) == expected)


def main(argv=None):
    parser = argparse.ArgumentParser(description='worker 이미지 입력 검사 (작업 실행 아님)')
    parser.add_argument('--artifact-only', action='store_true')
    parser.add_argument('--prepare-model', type=Path)
    args = parser.parse_args(argv)
    try:
        base = os.environ.get('WORKER_BASE_REFERENCE', '')
        require(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', base))
        lock = read_json(Path('/opt/worker/model-lock.json'))
        target = Path('/opt/models') / lock['revision']
        if args.prepare_model is not None:
            artifact_check(args.prepare_model, lock)
            target.mkdir(parents=True, exist_ok=False)
            for entry in lock['files']:
                output = target / entry['path']
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.prepare_model / entry['path'], output)
        artifact_check(target, lock)
        if not args.artifact_only:
            runtime_check(read_json(Path('/opt/worker/runtime-lock.json')), base)
    except Exception:
        print('WORKER_IMAGE_INPUT_INVALID', file=sys.stderr)
        return 1
    print('WORKER_ARTIFACT_VERIFIED' if args.artifact_only else 'WORKER_IMAGE_INPUTS_VERIFIED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
