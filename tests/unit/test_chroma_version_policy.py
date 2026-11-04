"""Chroma 설치 허용 범위(#91): dev·local 두 선언이 같은 값이고, 문서가 허용 범위와 검증 범위를 구분하는지 확인한다."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RANGE = 'chromadb>=1.5.9,<1.6'


def chroma_specs():
    extras = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['optional-dependencies']
    return {name: [d for d in deps if d.startswith('chromadb')] for name, deps in extras.items()
            if any(d.startswith('chromadb') for d in deps)}


def test_dev_and_local_extras_declare_the_same_bounded_range():
    specs = chroma_specs()
    assert set(specs) == {'dev', 'local'}  # chromadb를 쓰는 extra는 이 둘뿐이다
    assert all(value == [RANGE] for value in specs.values())


def test_no_extra_leaves_chromadb_unbounded():
    for deps in chroma_specs().values():
        assert all(re.fullmatch(r'chromadb>=\d+\.\d+\.\d+,<\d+\.\d+', d) for d in deps)


def test_policy_doc_separates_install_range_from_verified_version():
    text = (ROOT / 'doc/setup/chroma-support-policy.md').read_text(encoding='utf-8')
    assert RANGE in text and '1.5.9' in text
    assert '종료 동작 **미확인**' in text
    assert 'CHROMA_CLOSE_UNSUPPORTED' in text and '#135' in text
