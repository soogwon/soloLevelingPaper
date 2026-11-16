"""#517: 실제 합성 PDF + 제품 번역/gate + 대역 HTTP/임베더. 모델·외부 네트워크 금지."""

import builtins
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
import socket
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.provider_dispatch import (
    AttemptPermit, DispatchBudgetExceeded, DispatchError, DispatchIntent, ProviderDispatch,
)
from solo_leveling.application.remote_processing import RemoteProcessingStages
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.translation.retry import TranslationRetryPolicy
from solo_leveling.domain.models import PaperSection
from solo_leveling.domain.remote_processing import (
    EmbeddingBatch, EmbeddingSpec, ParsedDocument, ProcessingError,
    ProcessingLimits, ProcessingSource,
)
from solo_leveling.infrastructure.generation.gated_responses import GatedResponsesTransport, PreparedProviderAttempt
from solo_leveling.infrastructure.parsing.processing_parser import ProcessingPdfParser
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, OpenAITranslationProvider
from solo_leveling.infrastructure.translation.processing_translation import GatedProcessingTranslation
from tests.fixtures.pdf_builder import build_minimal_pdf, build_styled_pdf


@pytest.fixture(autouse=True)
def forbid_network_and_models(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('외부 호출·모델 실행 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(requests, 'post', forbidden)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in ('torch', 'sentence_transformers', 'transformers', 'onnxruntime'):
            forbidden()
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)


class Ledger:
    def __init__(self):
        self.requests, self.observed, self.unknowns = [], [], []
        self.allowed = True
        self.error = None

    def authorize(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return AttemptPermit(f'attempt-{len(self.requests)}', request) if self.allowed else None

    def observe(self, *args):
        self.observed.append(args)

    def unknown(self, attempt):
        self.unknowns.append(attempt)


def envelope(text):
    return {'status': 'completed', 'usage': {'input_tokens': 5, 'output_tokens': 2},
            'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': json.dumps({'text': text})}]}]}


@pytest.fixture
def setup():
    pdf = build_minimal_pdf(['The first synthetic page has a relay.', 'The second page has another depot.'])
    source = ProcessingSource('owner', 'job', 'paper', 'version', 'parse-new', 'translation-new',
                              'set-new', hashlib.sha256(pdf).hexdigest(), len(pdf))
    spec = EmbeddingSpec('synthetic-model', 'fixed-model', 'synthetic-tokenizer', 'fixed-tokenizer',
                         'mean', 'l2', 128, 2, 'cosine', 'float32', 'pytorch', 'synthetic-version', 'a' * 64)
    limits = ProcessingLimits(100_000, 20, 100)
    ledger = Ledger()
    dispatch = ProviderDispatch(ledger, DispatchIntent('owner', 'execution', 'lease', 'hash', 'storage', 'ai', 'notice'))
    response = Mock(status_code=200)
    response.json.return_value = envelope('합성 중계는 거점을 연결합니다.')
    post = Mock(return_value=response)

    def prepare(stage, payload, digest):
        raise AssertionError('unscoped prepare must not be used')

    transport = GatedResponsesTransport(dispatch, prepare, post=post)
    config = OpenAITranslationConfig('synthetic-service-secret', allow_external_api=True)
    provider = OpenAITranslationProvider(config, transport=transport)
    # retry 설정은 실제 서비스에 남기되 테스트에서만 대기를 제거한다.
    service = TranslationService(provider, retry_policy=TranslationRetryPolicy(
        base_delay_seconds=0, jitter_seconds=0), sleep=lambda _: None)
    def prepare_attempt(source, intent, request, attempt, stage, payload, digest):
        return PreparedProviderAttempt(f'{source.job_id}:{request.chunk_id}:{attempt}', digest, 100)

    translator = GatedProcessingTranslation(service, prepare_attempt=prepare_attempt)
    parser = Mock(wraps=ProcessingPdfParser())
    embedder = Mock()

    def embed(items, actual_spec):
        return EmbeddingBatch(actual_spec, tuple(i.chunk_id for i in items), tuple((1.0, 0.0) for _ in items))

    embedder.embed.side_effect = embed
    check = Mock(return_value=True)
    stages = RemoteProcessingStages(parser, translator, embedder, limits=limits,
        translation_settings=config.translation_settings, embedding_spec=spec, check_allowed=check)
    return stages, source, pdf, ledger, post, response, service


def run(setup):
    stages, source, pdf, *_ = setup
    return stages.run(source, pdf)


def test_real_pdf_to_translations_and_vectors_preserves_source_without_publishing(setup, capsys):
    stages, source, pdf, ledger, post, _, _ = setup
    before = deepcopy(source), pdf[:]
    result = run(setup)
    assert result.status == 'prepared'
    assert result.source == before[0] == source and pdf == before[1]
    assert result.document.page_count == 2 and result.empty_pages == ()
    assert [c.pdf_page for c in result.document.chunks] == [1, 2]
    assert [c.chunk_index for c in result.document.chunks] == [0, 1]
    assert all(c.parse_revision_id == 'parse-new' for c in result.document.chunks)
    assert 'first synthetic page' in result.document.chunks[0].original_text
    assert 'second page' in result.document.chunks[1].original_text
    assert all(c.printed_page_label is None for c in result.document.chunks)
    assert result.embeddings.chunk_ids == tuple(c.chunk_id for c in result.document.chunks)
    assert len(result.embeddings.vectors) == len(result.translations) == 2
    assert all(t.text == '합성 중계는 거점을 연결합니다.' and t.failure_code is None for t in result.translations)
    assert len(ledger.requests) == len(ledger.observed) == post.call_count == 2
    assert all(r.stage == 'translation' for r in ledger.requests)
    assert stages.embedder.embed.call_args.args[1] == stages.spec
    assert [c.args[1] for c in stages.check_allowed.call_args_list] == [
        'parse', 'translation', 'translation', 'translation', 'embedding', 'result']
    output = capsys.readouterr()
    for secret in ('synthetic-service-secret', 'The first synthetic page', '합성 중계는'):
        assert secret not in output.out + output.err + repr(result)


def test_real_styled_pdf_keeps_sections_and_empty_page_without_invented_labels(setup):
    stages, source, *_ = setup
    pdf = build_styled_pdf([[(12, False, 'A synthetic paper introduction.')],
                           [(16, True, '2 Methods'), (12, False, 'A synthetic relay method.')], []])
    source = replace(source, pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_size=len(pdf))
    result = stages.run(source, pdf)
    assert result.document.page_count == 3 and result.empty_pages == (3,)
    assert len(result.document.sections) == 1
    section = result.document.sections[0]
    assert section.parse_revision_id == source.parse_revision_id
    assert section.start_pdf_page == section.end_pdf_page == 2
    assert result.document.chunks[1].section_id == section.section_id


@pytest.mark.parametrize('changes,code', [
    ({'pdf_sha256': 'b' * 64}, 'PROCESSING_PDF_MISMATCH'),
    ({'pdf_size': 1}, 'PROCESSING_PDF_MISMATCH'),
    ({'pdf_size': True}, 'PROCESSING_INPUT_INVALID'),
    ({'pdf_sha256': 'bad'}, 'PROCESSING_INPUT_INVALID'),
    ({'job_id': ''}, 'PROCESSING_INPUT_INVALID'),
])
def test_bad_input_fails_before_parsing_translation_or_embedding(setup, changes, code):
    stages, source, pdf, _, post, *_ = setup
    with pytest.raises(ProcessingError, match=f'^{code}$'):
        stages.run(replace(source, **changes), pdf)
    stages.parser.parse.assert_not_called()
    stages.embedder.embed.assert_not_called()
    post.assert_not_called()


@pytest.mark.parametrize('field,value', [('max_pdf_bytes', 1), ('max_pages', 1), ('max_chunks', 1)])
def test_explicit_limits_stop_downstream_before_provider(setup, field, value):
    stages, _, _, _, post, *_ = setup
    stages.limits = replace(stages.limits, **{field: value})
    with pytest.raises(ProcessingError, match='^PROCESSING_LIMIT_EXCEEDED$'):
        run(setup)
    post.assert_not_called()
    stages.embedder.embed.assert_not_called()


def test_empty_pdf_stops_before_translation(setup):
    stages, source, _, _, post, *_ = setup
    pdf = build_minimal_pdf([''])
    with pytest.raises(ProcessingError, match='^PROCESSING_EMPTY_DOCUMENT$'):
        stages.run(replace(source, pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_size=len(pdf)), pdf)
    post.assert_not_called()


@pytest.mark.parametrize('header', [False, True])
def test_invalid_pdf_returns_safe_code_without_provider(setup, header):
    stages, source, _, _, post, *_ = setup
    pdf = (b'%PDF-1.4\n' if header else b'') + b'private-corrupt-body'
    with pytest.raises(ProcessingError, match='^PROCESSING_PDF_UNREADABLE$'):
        stages.run(replace(source, pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_size=len(pdf)), pdf)
    post.assert_not_called()


def test_encrypted_pdf_uses_fixed_error_and_no_provider(setup, monkeypatch):
    from solo_leveling.infrastructure.parsing import processing_parser
    from solo_leveling.infrastructure.parsing.pdf_extractor import PdfExtractionError
    monkeypatch.setattr(processing_parser, 'extract_pages', Mock(side_effect=PdfExtractionError(
        'pdf_encrypted', 'private-exception-body')))
    with pytest.raises(ProcessingError, match='^PROCESSING_PDF_ENCRYPTED$'):
        run(setup)
    setup[4].assert_not_called()


@pytest.mark.parametrize('failure', ['unknown', 'budget', 'denied', 'bad-response'])
def test_gate_failures_do_not_retry_translate_next_chunk_or_embed(setup, failure):
    stages, _, _, ledger, post, response, _ = setup
    if failure == 'unknown':
        post.side_effect = requests.Timeout('private-provider-body')
    elif failure == 'budget':
        ledger.error = DispatchBudgetExceeded('calls')
    elif failure == 'denied':
        ledger.allowed = False
    else:
        response.json.return_value = {'private': 'missing-usage'}
    with pytest.raises(DispatchError):
        run(setup)
    assert len(ledger.requests) == 1
    assert post.call_count == (0 if failure in ('budget', 'denied') else 1)
    assert len(ledger.unknowns) == (1 if failure in ('unknown', 'bad-response') else 0)
    stages.embedder.embed.assert_not_called()


def test_partial_translation_returns_failures_without_embedding(setup):
    stages, _, _, ledger, post, response, _ = setup
    response.json.side_effect = [envelope(''), envelope('나머지 합성 문장입니다.')]
    result = run(setup)
    assert result.status == 'translation_incomplete' and result.embeddings is None
    assert result.translations[0].failure_code.value == 'empty_translation'
    assert result.translations[0].text is None
    assert result.translations[1].text == '나머지 합성 문장입니다.'
    assert len(ledger.observed) == post.call_count == 2
    stages.embedder.embed.assert_not_called()


@pytest.mark.parametrize('stop_stage,expected_posts', [('parse', 0), ('translation', 0), ('embedding', 2), ('result', 2)])
def test_checkpoint_denial_stops_result_or_next_stage(setup, stop_stage, expected_posts):
    stages, _, _, _, post, *_ = setup
    stages.check_allowed.side_effect = lambda source, stage: stage != stop_stage
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)
    assert post.call_count == expected_posts
    if stop_stage != 'result':
        stages.embedder.embed.assert_not_called()


def test_revocation_between_chunks_stops_second_attempt(setup):
    stages, _, _, _, post, *_ = setup
    stages.check_allowed.side_effect = [True, True, True, False]
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)
    assert post.call_count == 1
    stages.embedder.embed.assert_not_called()


def test_checkpoint_error_is_safe_and_does_not_start_provider(setup):
    stages, _, _, _, post, *_ = setup
    stages.check_allowed.side_effect = RuntimeError('private-owner-and-body')
    with pytest.raises(ProcessingError, match='^PROCESSING_CHECK_UNAVAILABLE$'):
        run(setup)
    post.assert_not_called()


@pytest.mark.parametrize('value', [1, 'true', None])
def test_checkpoint_truthy_values_are_not_authorization(setup, value):
    setup[0].check_allowed.return_value = value
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)


@pytest.mark.parametrize('change', ['spec', 'dimension', 'nan', 'inf', 'bool', 'zero', 'norm',
                                    'large-int', 'count', 'ids', 'mutable'])
def test_vector_integrity_blocks_prepared_result(setup, change):
    stages, *_ = setup
    def embed(items, spec):
        batch = EmbeddingBatch(spec, tuple(i.chunk_id for i in items), ((1.0, 0.0),) * len(items))
        if change == 'spec': return replace(batch, spec=replace(spec, model_revision='different'))
        if change == 'ids': return replace(batch, chunk_ids=tuple(reversed(batch.chunk_ids)))
        if change == 'count': return replace(batch, vectors=batch.vectors[:-1])
        if change == 'mutable': return replace(batch, vectors=[list(v) for v in batch.vectors])
        vector = {'dimension': (1.0,), 'nan': (math.nan, 0), 'inf': (math.inf, 0), 'bool': (True, 0),
                  'zero': (0, 0), 'norm': (2, 0), 'large-int': (10**400, 0)}[change]
        return replace(batch, vectors=(vector,) + batch.vectors[1:])
    stages.embedder.embed.side_effect = embed
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_INVALID$'):
        run(setup)


@pytest.mark.parametrize('field,value', [('runtime', 'onnx'), ('precision', 'int8'), ('dimension', True),
                                       ('normalization', 'invented'), ('model_revision', ''),
                                       ('artifact_sha256', 'missing'), ('distance', 'l2')])
def test_embedding_spec_must_be_explicit_and_pytorch_fp32(setup, field, value):
    stages = setup[0]
    with pytest.raises(ProcessingError, match='^PROCESSING_CONFIG_INVALID$'):
        RemoteProcessingStages(stages.parser, stages.translator, stages.embedder, limits=stages.limits,
            translation_settings=stages.settings, embedding_spec=replace(stages.spec, **{field: value}),
            check_allowed=stages.check_allowed)


def test_missing_gate_is_rejected_before_processing_and_owner_mismatch_before_http(setup):
    stages, source, pdf, _, post, _, service = setup
    with pytest.raises(ProcessingError, match='^PROCESSING_TRANSLATION_GATE_REQUIRED$'):
        GatedProcessingTranslation(TranslationService(OpenAITranslationProvider(service.provider.config)),
                                   prepare_attempt=lambda *args: None)
    with pytest.raises(ProcessingError, match='^PROCESSING_TRANSLATION_CONFIG_MISMATCH$'):
        stages.run(replace(source, owner_user_id='other-owner'), pdf)
    post.assert_not_called()


def test_changing_external_provider_does_not_enable_direct_http(setup):
    stages, _, _, _, post, _, service = setup
    def check(source, stage):
        if stage == 'translation' and post.call_count:
            service.provider.transport = None
        return True
    stages.check_allowed.side_effect = check
    result = run(setup)
    assert result.status == 'prepared' and post.call_count == 2


@pytest.mark.parametrize('change', ['revision', 'index', 'page', 'section', 'duplicate'])
def test_malformed_parsing_never_reaches_provider(setup, change):
    stages, source, pdf, _, post, *_ = setup
    doc = ProcessingPdfParser().parse(pdf, source.parse_revision_id)
    first, second = doc.chunks
    if change == 'revision': first = replace(first, parse_revision_id='other')
    elif change == 'index': first = replace(first, chunk_index=True)
    elif change == 'page': second = replace(second, pdf_page=3)
    elif change == 'section': first = replace(first, section_id=['invalid'])
    else: second = replace(second, chunk_id=first.chunk_id)
    stages.parser.parse.return_value = replace(doc, chunks=(first, second))
    stages.parser.parse.side_effect = None
    with pytest.raises(ProcessingError, match='^PROCESSING_PARSE_INVALID$'):
        run(setup)
    post.assert_not_called()


@pytest.mark.parametrize('stage,code', [('parse', 'PROCESSING_PARSE_FAILED'),
                                      ('translation', 'PROCESSING_TRANSLATION_FAILED'),
                                      ('embedding', 'PROCESSING_EMBEDDING_FAILED')])
def test_unexpected_stage_errors_do_not_expose_provider_or_pdf_body(setup, stage, code):
    stages = setup[0]
    target = {'parse': stages.parser.parse, 'translation': None, 'embedding': stages.embedder.embed}[stage]
    if target is None:
        stages.translator = Mock()
        target = stages.translator.translate
    target.side_effect = RuntimeError('secret-body-and-path')
    with pytest.raises(ProcessingError, match=f'^{code}$'):
        run(setup)


def test_parser_ids_and_dispatch_keys_are_bound_to_fixed_revision_and_chunk_not_body(setup):
    stages, source, _, ledger, post, *_ = setup
    pdf = build_minimal_pdf(['Identical synthetic body.', 'Identical synthetic body.'])
    source = replace(source, pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_size=len(pdf))
    first = stages.run(source, pdf)
    second = ProcessingPdfParser().parse(pdf, source.parse_revision_id)
    assert first.document == second
    assert second.chunks[0].original_text == second.chunks[1].original_text
    assert second.chunks[0].chunk_id != second.chunks[1].chunk_id
    assert [r.dispatch_key for r in ledger.requests] == [f'job:{c.chunk_id}:1' for c in second.chunks]
    assert post.call_count == 2
    # 다른 파싱 리비전은 같은 본문이라도 다른 청크 신원을 갖는다.
    changed = ProcessingPdfParser().parse(pdf, 'parse-other')
    assert not {c.chunk_id for c in changed.chunks} & {c.chunk_id for c in second.chunks}


def test_pre_send_retry_rechecks_checkpoint_before_retrying(setup, monkeypatch):
    from solo_leveling.application.translation.ports import ProviderFailure, TranslationProviderUnavailable
    stages, _, _, _, post, *_ = setup
    stages.check_allowed.side_effect = [True, True, True, False]
    # 실제 HTTP보다 앞의 합성 장애. 이미 전송한 timeout/unknown의 재시도와 구분한다.
    translate = Mock(side_effect=TranslationProviderUnavailable('private-body', code=ProviderFailure.SERVER))
    monkeypatch.setattr(OpenAITranslationProvider, 'translate', translate)
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)
    assert translate.call_count == 1
    post.assert_not_called()
    stages.embedder.embed.assert_not_called()


@pytest.mark.parametrize('change', ['revision', 'source', 'settings', 'order'])
def test_translation_result_must_match_allocated_revision_original_and_order(setup, change):
    from solo_leveling.domain.translation import TranslationRequest, TranslationBatchResult
    from solo_leveling.domain.translation import ChunkTranslationResult
    from solo_leveling.domain.models import TranslationRevision
    stages, source, pdf, *_ = setup
    chunks = ProcessingPdfParser().parse(pdf, source.parse_revision_id).chunks
    items = tuple(ChunkTranslationResult(TranslationRequest(c.chunk_id, c.parse_revision_id, c.original_text),
                                        '합성 번역입니다.') for c in chunks)
    revision = TranslationRevision(source.translation_revision_id, source.parse_revision_id,
                                   stages.settings.provider, stages.settings.model)
    batch = TranslationBatchResult(revision, stages.settings, items)
    if change == 'revision': batch.revision.translation_revision_id = 'other-revision'
    elif change == 'source':
        batch = replace(batch, items=(replace(items[0], request=replace(items[0].request,
                          original_text='A different source.')), items[1]))
    elif change == 'settings': batch = replace(batch, settings=replace(stages.settings, prompt_version='other'))
    else: batch = replace(batch, items=tuple(reversed(items)))
    stages.translator = Mock()
    stages.translator.translate.return_value = batch
    with pytest.raises(ProcessingError, match='^PROCESSING_TRANSLATION_INVALID$'):
        run(setup)
    stages.embedder.embed.assert_not_called()


@pytest.mark.parametrize('change', ['revision', 'range', 'ordinal', 'parent', 'title', 'bool-index'])
def test_section_catalog_integrity_is_checked_before_translation(setup, change):
    stages, source, pdf, _, post, *_ = setup
    chunks = ProcessingPdfParser().parse(pdf, source.parse_revision_id).chunks
    section = PaperSection('section', source.parse_revision_id, 'Synthetic section', 1, 1, 1, 2,
                           start_chunk_index=0, end_chunk_index=1)
    changes = {'revision': {'parse_revision_id': 'other'}, 'range': {'end_chunk_index': 0},
               'ordinal': {'ordinal': True}, 'parent': {'parent_section_id': 'missing'}, 'title': {'title': ''},
               'bool-index': {'end_chunk_index': True}}
    section = replace(section, **changes[change])
    stages.parser.parse.return_value = ParsedDocument(2, tuple(replace(c, section_id='section') for c in chunks),
                                                       (section,))
    with pytest.raises(ProcessingError, match='^PROCESSING_PARSE_INVALID$'):
        run(setup)
    post.assert_not_called()


def test_partial_result_is_also_checked_before_return(setup):
    stages, _, _, _, _, response, _ = setup
    response.json.return_value = envelope('')
    stages.check_allowed.side_effect = lambda source, stage: stage != 'result'
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)
    stages.embedder.embed.assert_not_called()


@pytest.mark.parametrize('mode', ['success', 'partial', 'unknown', 'stopped', 'embedding-error'])
def test_original_context_database_and_vectors_are_untouched(setup, tmp_path, monkeypatch, mode):
    import sqlite3
    original = tmp_path / 'original.sqlite'
    with sqlite3.connect(original) as conn:
        conn.execute('create table contexts (id text, publication text)')
        conn.execute("insert into contexts values ('old-context', 'old-publication')")
    vectors = tmp_path / 'original-vectors'
    vectors.write_bytes(b'synthetic old vector snapshot')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    def forbidden(*args, **kwargs):
        pytest.fail('처리 단계에서 DB 연결 금지')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    stages, _, _, _, _, response, _ = setup
    if mode == 'partial': response.json.return_value = envelope('')
    elif mode == 'unknown': response.json.return_value = {'private': 'unknown-usage'}
    elif mode == 'stopped': stages.check_allowed.side_effect = lambda source, stage: stage != 'result'
    elif mode == 'embedding-error': stages.embedder.embed.side_effect = RuntimeError('private embedding failure')
    if mode in ('unknown', 'stopped', 'embedding-error'):
        with pytest.raises((DispatchError, ProcessingError)):
            run(setup)
    else:
        result = run(setup)
        assert result.status == ('prepared' if mode == 'success' else 'translation_incomplete')
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_embedding_stage_stop_reason_is_not_replaced_by_generic_failure(setup):
    setup[0].embedder.embed.side_effect = ProcessingError('PROCESSING_NOT_ALLOWED')
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)


def test_missing_or_invalid_attempt_preparation_never_uses_unscoped_http(setup):
    stages, _, _, _, post, _, service = setup
    with pytest.raises(ProcessingError, match='^PROCESSING_CONFIG_INVALID$'):
        GatedProcessingTranslation(service, prepare_attempt=None)
    stages.translator.prepare_attempt = lambda *args: None
    with pytest.raises(DispatchError, match='^DISPATCH_PAYLOAD_INVALID$'):
        run(setup)
    post.assert_not_called()
    stages.embedder.embed.assert_not_called()


def test_unknown_blocks_another_run_on_same_transport_without_another_post(setup):
    _, _, _, ledger, post, response, _ = setup
    response.json.return_value = {'private': 'no-usage'}
    with pytest.raises(DispatchError, match='^DISPATCH_USAGE_UNKNOWN$'):
        run(setup)
    with pytest.raises(DispatchError, match='^DISPATCH_BLOCKED$'):
        run(setup)
    assert len(ledger.requests) == post.call_count == len(ledger.unknowns) == 1


def test_bad_translation_body_with_known_usage_is_not_reclassified_as_unbilled(setup):
    stages, _, _, ledger, post, response, _ = setup
    body = envelope('정상 형식 대신 민감한 응답')
    body['output'][0]['content'][0]['text'] = 'private invalid JSON'
    response.json.return_value = body
    with pytest.raises(ProcessingError, match='^PROCESSING_TRANSLATION_FAILED$'):
        run(setup)
    assert post.call_count == len(ledger.observed) == 1
    assert ledger.unknowns == []
    stages.embedder.embed.assert_not_called()


def test_shared_transport_reconfiguration_cannot_switch_owner_or_http_mid_stage(setup):
    stages, _, _, ledger, post, response, service = setup
    transport = service.provider.transport
    foreign_ledger = Ledger()
    foreign_post = Mock(return_value=response)
    foreign_dispatch = ProviderDispatch(foreign_ledger,
        DispatchIntent('other-owner', 'other-execution', 'other-lease', 'other-hash', 'storage', 'ai', 'notice'))
    calls = 0
    def check(source, stage):
        nonlocal calls
        calls += 1
        if calls == 3:  # adapter 검증 후 첫 청크 직전의 checkpoint
            transport.dispatch = foreign_dispatch
            transport.post = foreign_post
        return True
    stages.check_allowed.side_effect = check
    assert run(setup).status == 'prepared'
    assert post.call_count == len(ledger.requests) == 2
    assert all(r.intent.owner_user_id == 'owner' for r in ledger.requests)
    assert foreign_ledger.requests == []
    foreign_post.assert_not_called()


def test_embedder_cannot_relax_l2_spec_during_running_job(setup):
    stages = setup[0]
    def embed(items, spec):
        stages.spec = replace(spec, normalization='none')
        return EmbeddingBatch(stages.spec, tuple(i.chunk_id for i in items), ((2.0, 0.0),) * len(items))
    stages.embedder.embed.side_effect = embed
    with pytest.raises(ProcessingError, match='^PROCESSING_EMBEDDING_INVALID$'):
        run(setup)


@pytest.mark.parametrize('changed', ['intent', 'ledger'])
@pytest.mark.parametrize('after_first', [False, True])
def test_dispatch_identity_or_ledger_change_stops_before_next_reservation(setup, changed, after_first):
    stages, _, _, ledger, post, _, service = setup
    dispatch = service.provider.transport.dispatch
    other_ledger = Ledger()
    def check(source, stage):
        if stage == 'translation' and stages.check_allowed.call_count == (4 if after_first else 3):
            if changed == 'intent': dispatch.intent = replace(dispatch.intent, execution_id='other-execution')
            else: dispatch.ledger = other_ledger
        return True
    stages.check_allowed.side_effect = check
    with pytest.raises(ProcessingError, match='^PROCESSING_TRANSLATION_CONFIG_MISMATCH$'):
        run(setup)
    assert post.call_count == len(ledger.requests) == int(after_first)
    assert other_ledger.requests == []
    stages.embedder.embed.assert_not_called()


def test_prepare_port_receives_actual_intent_and_rejects_wrong_job_mapping(setup):
    stages, source, pdf, ledger, post, _, service = setup
    seen = []
    def prepare(src, intent, request, attempt, stage, payload, digest):
        seen.append((src, intent, request, attempt, stage))
        # A의 신뢰 포트가 source↔실제 실행을 대조하는 합성 예. 실제 DB 검증은 아니다.
        if src.job_id != 'job' or intent.execution_id != 'execution':
            raise ValueError('private scope detail')
        return PreparedProviderAttempt(f'{src.job_id}:{request.chunk_id}:{attempt}', digest, 100)
    stages.translator.prepare_attempt = prepare
    with pytest.raises(DispatchError, match='^DISPATCH_PREPARATION_UNAVAILABLE$'):
        stages.run(replace(source, job_id='other-job'), pdf)
    assert seen[0][1] == service.provider.transport.dispatch.intent
    assert seen[0][3:] == (1, 'translation')
    assert ledger.requests == []
    post.assert_not_called()


def test_revocation_during_prepare_is_checked_by_gate_before_transmission(setup):
    stages, _, _, ledger, post, *_ = setup
    original_prepare = stages.translator.prepare_attempt
    def prepare(*args):
        ledger.allowed = False
        return original_prepare(*args)
    stages.translator.prepare_attempt = prepare
    with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
        run(setup)
    assert len(ledger.requests) == 1 and ledger.observed == []
    post.assert_not_called()


def test_parser_cannot_raise_page_limit_during_running_job(setup):
    stages, source, pdf, _, post, *_ = setup
    document = ProcessingPdfParser().parse(pdf, source.parse_revision_id)
    def parse(*args):
        stages.limits = replace(stages.limits, max_pages=100)
        return replace(document, page_count=21)
    stages.parser.parse.side_effect = parse
    with pytest.raises(ProcessingError, match='^PROCESSING_LIMIT_EXCEEDED$'):
        run(setup)
    post.assert_not_called()


def test_final_checkpoint_cannot_be_replaced_mid_stage(setup):
    stages = setup[0]
    original_check = stages.check_allowed
    original_check.side_effect = lambda source, stage: stage != 'result'
    original_embed = stages.embedder.embed.side_effect
    def embed(*args):
        stages.check_allowed = Mock(return_value=True)
        return original_embed(*args)
    stages.embedder.embed.side_effect = embed
    with pytest.raises(ProcessingError, match='^PROCESSING_NOT_ALLOWED$'):
        run(setup)
    assert original_check.call_args.args[1] == 'result'


def test_real_parser_configuration_is_immutable():
    from dataclasses import FrozenInstanceError
    parser = ProcessingPdfParser()
    with pytest.raises(FrozenInstanceError):
        parser.chunk_size = 1
