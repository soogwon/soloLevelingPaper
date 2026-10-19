"""요약 생성 연결(M1-05, #50): 가짜 생성기와 OpenAI 응답 대체로 근거 검증·저장을 확인한다."""
import json
from contextlib import closing
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
from solo_leveling.application.summary.service import DraftItem, NoSupportedSummary, SummaryService
from solo_leveling.domain.paper_summary import SummaryValidationError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIGenerationError, OpenAIGenerationSettings, OpenAISummaryGenerator,
)
from tests.unit.test_learning_persistence import setup_db, publish


class FakeGenerator:
    def __init__(self, make):
        self.make, self.calls = make, []

    def generate_summary(self, evidence):
        self.calls.append([e.evidence_id for e in evidence])
        return self.make(evidence)


def count(db, table):
    with closing(get_connection(db)) as conn:
        return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


@pytest.fixture
def ctx(setup_db):
    db, chunks = setup_db
    return db, publish(db, chunks).context_id


def test_generate_saves_supported_items_and_only_their_evidence(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [DraftItem('claim', '주장', (ev[0].evidence_id,)),
                                    DraftItem('result', '결과', (ev[0].evidence_id, ev[1].evidence_id))])
    result = SummaryService(db, gen).generate(cid)
    assert result.dropped_count == 0
    assert [i.kind for i in result.summary.items] == ['claim', 'result']
    detail = repo.get_summary_evidence(db, result.summary).evidence
    assert [(d.quote_original, d.pdf_page) for d in detail] == [('First.', 1), ('Second.', 2)]
    assert repo.get_summary(db, cid).summary_id == result.summary.summary_id


def test_unsupported_and_malformed_items_are_dropped(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [
        DraftItem('claim', '정상', (ev[0].evidence_id,)),
        DraftItem('method', '없는 근거', ('invented',)),
        DraftItem('result', '근거 없음', ()),
        DraftItem('opinion', '종류 오류', (ev[0].evidence_id,)),
        DraftItem('limitation', '  ', (ev[0].evidence_id,)),
    ])
    result = SummaryService(db, gen).generate(cid)
    assert [i.text for i in result.summary.items] == ['정상']
    assert result.dropped_count == 4
    assert count(db, 'evidences') == 1  # 쓰이지 않은 청크의 근거는 저장하지 않는다


def test_nothing_supported_saves_nothing(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [DraftItem('claim', '근거 위조', ('invented',))])
    with pytest.raises(NoSupportedSummary):
        SummaryService(db, gen).generate(cid)
    assert (count(db, 'summaries'), count(db, 'summary_items'), count(db, 'evidences')) == (0, 0, 0)


def test_empty_generation_saves_nothing(ctx):
    db, cid = ctx
    with pytest.raises(NoSupportedSummary):
        SummaryService(db, FakeGenerator(lambda ev: [])).generate(cid)
    assert count(db, 'summaries') == 0


def test_generator_failure_propagates_without_saving(ctx):
    db, cid = ctx
    def boom(ev):
        raise GenerationUnavailable('down')
    with pytest.raises(GenerationUnavailable):
        SummaryService(db, FakeGenerator(boom)).generate(cid)
    assert count(db, 'summaries') == 0


def test_calls_are_split_by_max_chunks(ctx):
    db, cid = ctx
    gen = FakeGenerator(lambda ev: [DraftItem('claim', f'요약 {len(gen.calls)}', (ev[0].evidence_id,))])
    result = SummaryService(db, gen, max_chunks_per_call=1).generate(cid)
    assert [len(c) for c in gen.calls] == [1, 1]
    assert len(result.summary.items) == 2


def test_section_scope_summarizes_only_that_section_and_groups_by_section(setup_db):
    db, chunks = setup_db
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET section_id='01-a' WHERE chunk_id='c1'")
        conn.execute("UPDATE chunks SET section_id='02-b' WHERE chunk_id='c2'")
    cid = publish(db, chunks).context_id
    gen = FakeGenerator(lambda ev: [DraftItem('claim', '요약', (ev[0].evidence_id,))])
    SummaryService(db, gen).generate(cid)  # 전체: 섹션별로 호출이 나뉜다
    assert [len(c) for c in gen.calls] == [1, 1]
    gen.calls.clear()
    result = SummaryService(db, gen).generate(cid, '02-b')
    assert [len(c) for c in gen.calls] == [1]
    assert repo.get_summary_evidence(db, result.summary).evidence[0].quote_original == 'Second.'
    assert sorted(repo.list_summary_scopes(db, cid)) == ['02-b', 'paper']


def test_unknown_section_and_unknown_context(ctx):
    db, cid = ctx
    svc = SummaryService(db, FakeGenerator(lambda ev: []))
    with pytest.raises(SummaryValidationError):
        svc.generate(cid, '99-none')
    with pytest.raises(Exception):
        svc.generate('missing')


# ── OpenAI 어댑터 ──────────────────────────────────────────────
def reply(body):
    response = Mock(status_code=200)
    response.json.return_value = {'status': 'completed', 'output': [{
        'type': 'message', 'role': 'assistant', 'status': 'completed',
        'content': [{'type': 'output_text', 'text': body if isinstance(body, str) else json.dumps(body)}]}]}
    return response


def settings(**kw):
    return OpenAIGenerationSettings('test-key', allow_external_api=True, **kw)


def test_openai_summary_end_to_end_with_stubbed_api(ctx, monkeypatch):
    db, cid = ctx
    sent = {}

    def post(url, **kwargs):
        sent.update(kwargs)
        ev = json.loads(kwargs['json']['input'][0]['content'])['evidence']
        return reply({'items': [{'kind': 'claim', 'text': '핵심 주장', 'evidence_ids': [ev[0]['evidence_id']]},
                                {'kind': 'method', 'text': '근거 위조', 'evidence_ids': ['invented']}]})
    monkeypatch.setattr(requests, 'post', post)
    result = SummaryService(db, OpenAISummaryGenerator(settings())).generate(cid)
    assert [i.text for i in result.summary.items] == ['핵심 주장'] and result.dropped_count == 1
    payload = sent['json']
    assert payload['store'] is False and payload['text']['format']['name'] == 'paper_summary'
    body = json.loads(payload['input'][0]['content'])
    assert set(body) == {'evidence'} and 'chunk_id' not in body['evidence'][0]


@pytest.mark.parametrize('body', ['not json', '{"items": 3}', '{"items": [{"kind": "claim"}]}', '[]'])
def test_openai_summary_rejects_malformed_output(monkeypatch, body):
    monkeypatch.setattr(requests, 'post', lambda *a, **k: reply(body))
    from solo_leveling.domain.evidence_qa import EvidenceInput
    ev = [EvidenceInput('e1', 'c1', '본문', 'text', None, 1)]
    with pytest.raises(GenerationFormatError):
        OpenAISummaryGenerator(settings()).generate_summary(ev)


def test_openai_summary_respects_external_api_flag_and_http_errors(monkeypatch):
    from solo_leveling.domain.evidence_qa import EvidenceInput
    ev = [EvidenceInput('e1', 'c1', '본문', 'text', None, 1)]
    with pytest.raises(GenerationUnavailable):
        OpenAISummaryGenerator(OpenAIGenerationSettings('k')).generate_summary(ev)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: Mock(status_code=429))
    with pytest.raises(OpenAIGenerationError):
        OpenAISummaryGenerator(settings()).generate_summary(ev)
