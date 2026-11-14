"""검수 KG 시드 검증과 학습 경로 계산 규칙을 확인한다."""

import copy
import json

import pytest

from solo_leveling.domain.knowledge_graph import (
    GOALS, SeedValidationError, build_graph, learning_path, parse_seed,
)
from solo_leveling.interfaces.cli.kg_check import main as kg_check
from solo_leveling.knowledge import load_graph

HASH = 'a' * 64


def concept(cid, *aliases):
    return {'id': cid, 'name_ko': f'{cid} 한글', 'name_en': cid.title(), 'aliases': list(aliases),
            'description_ko': f'{cid} 설명'}


def edge(source, target):
    return {'prerequisite': source, 'concept': target, 'rationale_ko': f'{source}→{target}',
            'source_reference': None}


def seed(**goal_overrides):
    """vector → dot → attention ← softmax, attention → transformer, extra(optional) ← softmax."""
    goals = {goal: {'targets': ['transformer'], 'optional': ['extra'],
                    'reasons': {'transformer': '목표', 'extra': '선택'}} for goal in GOALS}
    goals.update(goal_overrides)
    return {
        'schema_version': 1,
        'concepts': [concept('vector', '벡터'), concept('dot', '내적', 'Dot Product'), concept('softmax'),
                     concept('attention'), concept('transformer'), concept('extra')],
        'prerequisites': [edge('vector', 'dot'), edge('dot', 'attention'), edge('softmax', 'attention'),
                          edge('attention', 'transformer'), edge('softmax', 'extra')],
        'papers': [{'key': 'paper', 'label': 'Paper', 'file_sha256': HASH, 'source_url': None,
                    'concepts': [{'id': c, 'pdf_pages': [2, 1], 'section_hint': None}
                                 for c in ('attention', 'transformer', 'extra')],
                    'goals': goals}],
    }


def ids(path):
    return [step.concept.concept_id for step in path.steps]


def test_path_orders_prerequisites_before_targets_and_is_deterministic():
    graph = build_graph(seed(), 'rev')
    paper = graph.papers[0]
    path = learning_path(graph, paper, 'understand')
    assert ids(path) == ['vector', 'dot', 'softmax', 'attention', 'transformer']
    assert [s.role for s in path.steps] == ['required'] * 4 + ['target']
    assert path.steps[1].reason_ko == 'dot→attention'  # required의 이유는 다음 개념과의 관계
    assert path.steps[1].needed_for.concept_id == 'attention'
    assert path.steps[-1].reason_ko == '목표'
    assert path.steps[-1].needed_for is None
    assert path.steps[3].prerequisite_ids == ('dot', 'softmax')
    assert path.steps[3].locations.pdf_pages == (1, 2)
    assert ids(learning_path(graph, paper, 'understand')) == ids(path)


def test_expanded_adds_optional_after_core():
    graph = build_graph(seed(), 'rev')
    path = learning_path(graph, graph.papers[0], 'understand', mode='expanded')
    assert ids(path)[-1] == 'extra' and path.steps[-1].role == 'optional'
    # softmax는 target 경로에도 필요하므로 required가 우선한다.
    assert {s.concept.concept_id: s.role for s in path.steps}['softmax'] == 'required'


def test_known_concepts_prune_their_subtree_and_report_skips():
    graph = build_graph(seed(), 'rev')
    path = learning_path(graph, graph.papers[0], 'understand',
                         ['  DOT   product ', '모르는 개념'])
    assert ids(path) == ['softmax', 'attention', 'transformer']
    assert [(s.concept.concept_id, s.reason) for s in path.skipped] == [
        ('dot', 'known'), ('vector', 'only_needed_by_known')]
    assert path.unresolved == ('모르는 개념',)
    assert path.steps[1].prerequisite_ids == ('softmax',)


def test_known_target_leaves_nothing_to_learn():
    graph = build_graph(seed(), 'rev')
    path = learning_path(graph, graph.papers[0], 'skim', ['transformer'])
    assert path.steps == ()
    assert ('transformer', 'known') in [(s.concept.concept_id, s.reason) for s in path.skipped]


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d['prerequisites'].append(edge('transformer', 'vector')), '순환'),
    (lambda d: d['prerequisites'].append(edge('dot', 'dot')), '자기 자신'),
    (lambda d: d['prerequisites'].append(edge('dot', 'missing')), '없는 개념'),
    (lambda d: d['concepts'].append(concept('Bad_ID')), 'id는'),
    (lambda d: d['concepts'].append(concept('other', '내적')), '겹칩니다'),
    (lambda d: d['papers'][0]['goals']['skim'].update(targets=[]), 'target이 1개'),
    (lambda d: d['papers'][0]['goals']['skim'].update(optional=['transformer']), '동시에'),
    (lambda d: d['papers'][0]['goals']['skim'].update(targets=['vector']), 'concepts에 없습니다'),
    (lambda d: d['papers'][0]['goals']['skim']['reasons'].pop('extra'), 'reasons가 필요'),
    (lambda d: d['papers'][0].update(file_sha256='TODO'), '64자'),
    (lambda d: d['papers'][0]['goals'].pop('skim'), '모두 있어야'),
    (lambda d: d['papers'][0]['concepts'][0].update(pdf_pages=[0]), '양의 정수'),
    (lambda d: d['papers'][0]['concepts'][0].update(search_phrase='x' * 61), 'search_phrase'),
    (lambda d: d['papers'][0]['concepts'][0].update(search_phrase='두 줄\n문장'), 'search_phrase'),
    (lambda d: d['papers'][0]['concepts'][0].update(search_phrase='두 줄\r문장'), 'search_phrase'),
])
def test_invalid_seed_reports_every_problem(mutate, message):
    data = copy.deepcopy(seed())
    mutate(data)
    with pytest.raises(SeedValidationError) as error:
        build_graph(data, 'rev')
    assert any(message in problem for problem in error.value.problems)


def test_missing_hash_is_a_warning_and_revision_follows_content():
    data = seed()
    data['papers'][0]['file_sha256'] = None
    graph = parse_seed(json.dumps(data))
    assert graph.warnings and graph.paper_by_hash(HASH) is None
    assert parse_seed(json.dumps(data)).revision_id == graph.revision_id
    assert parse_seed(json.dumps(seed())).revision_id != graph.revision_id
    assert parse_seed(json.dumps(seed())).paper_by_hash(HASH.upper()).paper_key == 'paper'


def test_packaged_seed_is_valid_and_every_goal_has_a_path():
    graph = load_graph()
    assert {p.paper_key for p in graph.papers} == {'attention-is-all-you-need', 'bert', 'vision-transformer'}
    for paper in graph.papers:
        for goal in GOALS:
            path = learning_path(graph, paper, goal)
            assert path.steps and path.steps[-1].role in ('target', 'required')
            position = {s.concept.concept_id: s.order for s in path.steps}
            for step in path.steps:
                assert all(position[p] < step.order for p in step.prerequisite_ids)


def test_kg_check_cli_validates_and_matches_hashes(tmp_path, capsys):
    pdf = tmp_path / 'paper.pdf'
    pdf.write_bytes(b'%PDF-1.4 sample')
    import hashlib
    data = seed()
    data['papers'][0]['file_sha256'] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    seed_file = tmp_path / 'seed.json'
    seed_file.write_text(json.dumps(data), encoding='utf-8')
    assert kg_check([str(seed_file), '--hash', str(pdf)]) == 0
    assert 'paper와 일치' in capsys.readouterr().out
    data['prerequisites'].append(edge('transformer', 'vector'))
    seed_file.write_text(json.dumps(data), encoding='utf-8')
    assert kg_check([str(seed_file)]) == 1
    assert '순환' in capsys.readouterr().err


def test_malformed_seed_reports_multiple_problems_without_crashing_cli(tmp_path, capsys):
    data = seed()
    data['prerequisites'][0]['concept'] = []
    paper = data['papers'][0]
    paper.update(label=123, concepts=5)
    paper['goals']['skim']['targets'] = [{}]
    path = tmp_path / 'invalid.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert kg_check([str(path)]) == 1
    problems = capsys.readouterr().err
    assert all(field in problems for field in ('prerequisites', 'label', 'concepts', 'skim'))
    path.write_bytes(bytes([255]))
    assert kg_check([str(path)]) == 1
    assert 'UTF-8' in capsys.readouterr().err


def test_search_phrase_is_checked_against_listed_pages():
    from solo_leveling.interfaces.cli.kg_check import normalize_text, verify_phrases
    data = seed()
    data['papers'][0]['concepts'][0]['search_phrase'] = 'Scaled Dot-Product Attention'
    data['papers'][0]['concepts'][1]['search_phrase'] = 'Missing Term'
    paper = build_graph(data, 'rev').papers[0]
    assert paper.concepts['attention'].search_phrase == 'Scaled Dot-Product Attention'
    pages = [normalize_text('3.2.1 Scaled Dot-\nProduct  Attention'), normalize_text('nothing'),
             normalize_text('Missing ﬁ Term'), normalize_text('Missing Term')]
    problems = verify_phrases(paper, pages)
    assert problems == ['paper/transformer: "Missing Term"가 [1, 2]페이지에 없습니다(발견 페이지: 4).']


def test_arxiv_ids_ignore_version_suffix():
    from solo_leveling.domain.knowledge_graph import arxiv_base_id

    assert arxiv_base_id('https://arxiv.org/pdf/1706.03762v7') == '1706.03762'
    assert arxiv_base_id('https://arxiv.org/abs/2010.11929') == '2010.11929'
    assert arxiv_base_id('arXiv:1810.04805v2 [cs.CL] 24 May 2019') == '1810.04805'
    assert arxiv_base_id('no stamp here') is None
    graph = load_graph()
    assert graph.paper_by_arxiv('1706.03762').paper_key == 'attention-is-all-you-need'
    assert graph.paper_by_arxiv(None) is None


def test_reads_arxiv_stamp_from_reviewed_pdf():
    from pathlib import Path
    from solo_leveling.infrastructure.parsing.arxiv_stamp import read_arxiv_id

    pdf = Path(__file__).resolve().parents[2] / 'test_data/tmp_pdfs/075b9be9-5944-4e83-af4b-2add5dbdeb94.pdf'
    from hashlib import sha256
    digest = sha256(pdf.read_bytes()).hexdigest()
    assert read_arxiv_id(str(pdf), pdf_root=pdf.parent, expected_hash=digest) == '1706.03762'
    assert read_arxiv_id(str(pdf.parent / 'missing.pdf'), pdf_root=pdf.parent, expected_hash=digest) is None


def test_arxiv_lookup_rejects_external_links_and_changed_file(tmp_path):
    from hashlib import sha256
    from pathlib import Path
    from solo_leveling.infrastructure.parsing.arxiv_stamp import read_arxiv_id

    pdf = Path(__file__).resolve().parents[2] / 'test_data/tmp_pdfs/075b9be9-5944-4e83-af4b-2add5dbdeb94.pdf'
    digest = sha256(pdf.read_bytes()).hexdigest()
    root = tmp_path / 'pdfs'
    root.mkdir()
    link = root / 'linked.pdf'
    link.symlink_to(pdf)
    assert read_arxiv_id(str(link), pdf_root=root, expected_hash=digest) is None
    assert read_arxiv_id(str(pdf), pdf_root=root, expected_hash=digest) is None
    local = root / 'local.pdf'
    local.write_bytes(pdf.read_bytes())
    assert read_arxiv_id('local.pdf', pdf_root=root, expected_hash=digest) == '1706.03762'
    local.write_bytes(b'%PDF-replaced')
    assert read_arxiv_id('local.pdf', pdf_root=root, expected_hash=digest) is None


def test_arxiv_lookup_does_not_use_body_reference(tmp_path, monkeypatch):
    from hashlib import sha256
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import pdfplumber
    from solo_leveling.infrastructure.parsing.arxiv_stamp import read_arxiv_id

    source = tmp_path / 'paper.pdf'
    source.write_bytes(b'%PDF-test')
    parsed = MagicMock()
    parsed.__enter__.return_value.pages = [SimpleNamespace(chars=[
        {'text': 'See https://arxiv.org/pdf/1706.03762v7', 'upright': True}])]
    monkeypatch.setattr(pdfplumber, 'open', lambda stream: parsed)
    assert read_arxiv_id(str(source), pdf_root=tmp_path,
                         expected_hash=sha256(source.read_bytes()).hexdigest()) is None
