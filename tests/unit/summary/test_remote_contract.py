"""합성 근거만으로 요약 전송·동의·결과의 불변 경계를 검증한다."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding
from solo_leveling.application.summary.remote_contract import (
    NOTICE_VERSION, RemoteSummaryError, RemoteSummaryInput, SummaryConsent, SummarySource,
    require_summary_consents, validated_summary_payload,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BINDING = RemoteSearchBinding('owner', 'context', 'paper', 'version', 'publication',
                              'parse', 'translation', 'embedding', '{"model":"fixed"}')
SOURCE = SummarySource('e1', 'c1', 1, 'methods', 'Synthetic source.', '합성 근거.')


def make(**changes):
    args = dict(binding=BINDING, sources=(SOURCE,), scope='paper', section_id=None,
                total_scope_chunks=1, provider='synthetic', model='fixed', prompt_version='v1',
                language='ko', budget_policy_version='summary-budget-v1')
    return RemoteSummaryInput.create(**(args | changes))


def consents(request):
    return tuple(SummaryConsent('owner', 'context', request.input_hash, purpose,
                               NOTICE_VERSION, False, NOW + timedelta(seconds=1))
                 for purpose in ('summary_storage', 'summary_ai'))


def items():
    return [dict(kind='method', text='합성 결과', evidence_ids=['e1'])]


def test_roundtrip_same_hash_and_notice_without_source_text():
    request = make()
    restored = RemoteSummaryInput(json.dumps(json.loads(request.encoded), indent=2))
    assert restored.input_hash == request.input_hash
    assert restored.unpack() == request.unpack()
    notice = request.transfer_notice()
    assert notice['automatic_generation'] is False
    assert notice['storage_consent_required'] and notice['ai_consent_required']
    assert SOURCE.original_text not in json.dumps(notice)
    assert SOURCE.original_text not in repr(request)
    assert SOURCE.original_text not in repr(SOURCE)


@pytest.mark.parametrize('field', ['owner_user_id', 'context_id', 'paper_id', 'server_version_id',
    'publication_id', 'parse_revision_id', 'translation_revision_id', 'embedding_set_id'])
def test_each_binding_change_requires_new_consent_hash(field):
    assert make(binding=replace(BINDING, **{field: 'other'})).input_hash != make().input_hash


@pytest.mark.parametrize('field', ['provider', 'model', 'prompt_version', 'language', 'budget_policy_version'])
def test_processing_change_requires_new_hash(field):
    assert make(**{field: 'other'}).input_hash != make().input_hash


@pytest.mark.parametrize('field,value', [('original_text', 'Changed.'), ('translated_text', None),
    ('evidence_id', 'e2'), ('chunk_id', 'c2'), ('pdf_page', 2)])
def test_source_change_requires_new_hash(field, value):
    assert make(sources=(replace(SOURCE, **{field: value}),)).input_hash != make().input_hash


def test_selected_section_cannot_include_foreign_section():
    request = make(scope='section', section_id='methods')
    assert request.input_hash != make().input_hash
    with pytest.raises(RemoteSummaryError):
        make(scope='section', section_id='results')


@pytest.mark.parametrize('changes', [dict(total_scope_chunks=2), dict(total_scope_chunks=True),
    dict(sources=()), dict(sources=(SOURCE, SOURCE), total_scope_chunks=2),
    dict(scope='paper', section_id='methods'), dict(scope='unknown')])
def test_partial_or_duplicated_input_is_not_presented_as_whole_scope(changes):
    with pytest.raises(RemoteSummaryError):
        make(**changes)


@pytest.mark.parametrize('encoded', ['null', '[]', '{"schema_version":1,"schema_version":1}',
                                    '{"schema_version":NaN}', '{}'])
def test_bad_snapshot_has_only_fixed_error(encoded):
    with pytest.raises(RemoteSummaryError, match='^REMOTE_SUMMARY_INVALID$'):
        RemoteSummaryInput(encoded)


def test_explicit_purpose_consents_required():
    request = make()
    require_summary_consents(request, consents(request), now=NOW)
    assert validated_summary_payload(request, items(), generated_at=NOW)['verification_level'] == 'structural_only'


@pytest.mark.parametrize('changes', [dict(owner_user_id='other'), dict(context_id='other'),
    dict(input_hash='other'), dict(purpose=[]), dict(purpose='ai_provider'), dict(purpose='storage'),
    dict(notice_version='remote-question-v1'), dict(revoked=True), dict(revoked=0),
    dict(expires_at=NOW), dict(expires_at=NOW.replace(tzinfo=None))])
def test_foreign_revoked_expired_or_question_consent_rejected(changes):
    request = make()
    current = consents(request)
    with pytest.raises(RemoteSummaryError):
        require_summary_consents(request, (replace(current[0], **changes), current[1]), now=NOW)


def test_duplicate_consent_purpose_rejected():
    request = make()
    consent = consents(request)[0]
    with pytest.raises(RemoteSummaryError):
        require_summary_consents(request, (consent, consent), now=NOW)


@pytest.mark.parametrize('bad', [[], [{'kind':'claim', 'text':'invented', 'evidence_ids':[]}],
    [{'kind':'claim', 'text':'invented', 'evidence_ids':['foreign']}],
    [{'kind':'claim', 'text':'invented', 'evidence_ids':['e1', 'e1']}],
    [{'kind':'claim', 'text':'', 'evidence_ids':['e1']}],
    [{'kind':'claim', 'text':'invented', 'evidence_ids':['e1'], 'extra':True}]])
def test_entire_result_rejected_before_storage_for_invalid_item(bad):
    with pytest.raises(RemoteSummaryError):
        validated_summary_payload(make(), bad, generated_at=NOW)


def test_result_keeps_revision_and_defensive_copy():
    original = items()
    payload = validated_summary_payload(make(), original, generated_at=NOW)
    original[0]['evidence_ids'].append('foreign')
    assert payload['items'][0]['evidence_ids'] == ['e1']
    assert payload['binding']['publication_id'] == 'publication'
    assert payload['binding']['translation_revision_id'] == 'translation'
    assert payload['prompt_version'] == 'v1'


def test_partial_publication_cannot_be_presented_as_complete_summary_scope():
    with pytest.raises(RemoteSummaryError):
        make(binding=replace(BINDING, publication_policy='partial'))


def test_brief_summary_freezes_sample_and_validates_three_field_contract():
    request = make(prompt_version='remote-summary-v2', total_scope_chunks=98)
    notice = request.transfer_notice()
    assert (notice['source_count'], notice['total_scope_chunks']) == (1, 98)
    assert request.input_hash != make(prompt_version='remote-summary-v2').input_hash
    assert validated_summary_payload(request, items(), generated_at=NOW)['items'] == items()
    for invalid in (items() * 2, [dict(items()[0], kind='limitation')],
                    [dict(items()[0], evidence_ids=['outside'])]):
        with pytest.raises(RemoteSummaryError):
            validated_summary_payload(request, invalid, generated_at=NOW)


def test_brief_selection_uses_bounded_payload_and_preserves_document_order():
    from solo_leveling.application.summary.prompt import build_summary_payload
    from solo_leveling.interfaces.http.remote_summary_sources import select_brief_sources

    sources = tuple(SummarySource(f'e{i}', f'c{i}', i + 1, None, 'Synthetic evidence. ' * 19, None)
                    for i in range(98))
    config = make(prompt_version='remote-summary-v2').unpack()[2]
    selected = select_brief_sources(sources, config, max_input_tokens=4000, max_output_tokens=1024)
    assert 1 < len(selected) < len(sources)
    assert (selected[0], selected[-1]) == (sources[0], sources[-1])
    assert selected == tuple(source for source in sources if source in selected)
    payload = build_summary_payload(selected, config, 1024)
    assert len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 256 <= 4000
    assert all(source.evidence_id in {s['evidence_id'] for s in json.loads(
        payload['input'][1]['content'])['sources']} for source in selected)
    with pytest.raises(RemoteSummaryError, match='REMOTE_SUMMARY_SOURCE_TOO_LARGE'):
        select_brief_sources(sources, config, max_input_tokens=500, max_output_tokens=1024)
