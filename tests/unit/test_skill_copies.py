"""skills/ 아래 Skill이 쓰는 복사본이 서비스 원본과 같은지 확인한다.

Skill은 단독으로 올려 쓰므로 개념 지도 시드와 학습 경로 계산 코드를 복사해 담는다.
원본만 고치고 복사본을 잊으면 Skill과 MCP의 학습 순서가 달라지므로 CI에서 막는다.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / 'skills' / 'solo-leveling-guide'
PDF_TEXT_COPIES = ('solo-leveling-quiz', 'solo-leveling-textbook')


def test_seed_copy_matches_service_seed():
    original = ROOT / 'src' / 'solo_leveling' / 'knowledge' / 'seed.json'
    assert (GUIDE / 'data' / 'seed.json').read_bytes() == original.read_bytes()


def test_knowledge_graph_copy_matches_domain_module():
    original = (ROOT / 'src' / 'solo_leveling' / 'domain' / 'knowledge_graph.py').read_text(encoding='utf-8')
    copy = (GUIDE / 'scripts' / 'knowledge_graph.py').read_text(encoding='utf-8')
    header, _, body = copy.partition('\n')
    assert header.startswith('# 원본:')
    assert body == original


def test_pdf_text_script_is_identical_across_skills():
    source = (GUIDE / 'scripts' / 'pdf_text.py').read_bytes()
    for name in PDF_TEXT_COPIES:
        assert (ROOT / 'skills' / name / 'scripts' / 'pdf_text.py').read_bytes() == source, name


def test_pdf_text_uses_service_extraction_tolerances():
    service = (ROOT / 'src' / 'solo_leveling' / 'infrastructure' / 'parsing' / 'pdf_extractor.py').read_text(encoding='utf-8')
    skill = (GUIDE / 'scripts' / 'pdf_text.py').read_text(encoding='utf-8')
    for name, skill_name in (('TEXT_X_TOLERANCE', 'X_TOLERANCE'), ('TEXT_Y_TOLERANCE', 'Y_TOLERANCE')):
        expected = re.search(rf'^{name} = (.+)$', service, re.M).group(1)
        assert re.search(rf'^{skill_name} = (.+)$', skill, re.M).group(1) == expected, name


def test_skill_names_match_folders():
    for folder in ('solo-leveling-guide', 'solo-leveling-quiz', 'solo-leveling-textbook'):
        text = (ROOT / 'skills' / folder / 'SKILL.md').read_text(encoding='utf-8')
        assert re.search(rf'^name: {folder}$', text, re.M), folder
