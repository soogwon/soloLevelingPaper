"""원격 검색 어댑터가 연결되기 전 범위·점수·불량 결과 계약을 검증한다."""

from dataclasses import fields, replace

import pytest

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteRetrievalContractError, RemoteSearchBinding, RemoteSearchFilters, RemoteSearchHit,
    cosine_distance_score, validate_remote_hits,
)


@pytest.fixture
def binding():
    return RemoteSearchBinding('alice', 'ctx-old', 'paper', 'version', 'pub-old',
                               'parse-old', 'tr-old', 'set-old', 'fingerprint-old')


def hit(binding, key='a', distance=0.25, page=6, section='s4'):
    return RemoteSearchHit(binding, key, page, section, distance)


def test_score_preserves_cosine_order_without_claiming_rrf_compatibility():
    assert [cosine_distance_score(x) for x in (0, 1, 2)] == [1, 0.5, 1 / 3]


@pytest.mark.parametrize('value', [-1, 2.01, float('nan'), float('inf'), True, '0'])
def test_invalid_distance_is_fixed_error(value):
    with pytest.raises(RemoteRetrievalContractError, match='^REMOTE_DISTANCE_INVALID$'):
        cosine_distance_score(value)


@pytest.mark.parametrize('field', [f.name for f in fields(RemoteSearchBinding)])
def test_each_owner_context_publication_revision_and_model_boundary_is_checked(binding, field):
    changed = replace(binding, **{field: 'partial' if field == 'publication_policy' else 'foreign-private-id'})
    with pytest.raises(RemoteRetrievalContractError, match='^REMOTE_SCOPE_MISMATCH$'):
        validate_remote_hits(binding, RemoteSearchFilters(), (hit(changed),), 5)


@pytest.mark.parametrize('strict', [False, True])
def test_normal_search_never_expands_page_scope(binding, strict):
    filters = RemoteSearchFilters((6,), ('s4',), strict_pages=strict)
    for bad in [hit(binding, page=7), hit(binding, section='s5'), hit(binding, section=None)]:
        with pytest.raises(RemoteRetrievalContractError, match='REMOTE_FILTER_MISMATCH'):
            validate_remote_hits(binding, filters, (bad,), 5)


def test_partial_index_does_not_rebind_to_current_publication_or_claim_answer_status(binding):
    binding = replace(binding, publication_policy='partial')
    result = validate_remote_hits(binding, RemoteSearchFilters(), (hit(binding),), 5)
    assert result[0].hit.binding.publication_id == 'pub-old'
    assert result[0].hit.binding.embedding_set_id == 'set-old'
    assert not hasattr(result[0], 'answer_status')


@pytest.mark.parametrize('kind', ['duplicate', 'count', 'order', 'tie', 'bad-page', 'empty-id'])
def test_bad_adapter_results_fail_closed(binding, kind):
    rows, k = (hit(binding),), 2
    if kind == 'duplicate': rows = (hit(binding), hit(binding))
    elif kind == 'count': rows, k = (hit(binding), hit(binding, 'b')), 1
    elif kind == 'order': rows = (hit(binding, 'a', 1), hit(binding, 'b', 0))
    elif kind == 'tie': rows = (hit(binding, 'b'), hit(binding, 'a'))
    elif kind == 'bad-page': rows = (hit(binding, page=True),)
    else: rows = (hit(binding, key=''),)
    with pytest.raises(RemoteRetrievalContractError):
        validate_remote_hits(binding, RemoteSearchFilters(), rows, k)


def test_prefilter_then_top_k_oracle_detects_postfilter_recall_loss(binding):
    filters = RemoteSearchFilters((6,), ('s4',))
    foreign = hit(replace(binding, owner_user_id='bob'), 'foreign', 0)
    outside = hit(binding, 'outside', 0.01, page=7)
    allowed = hit(binding, 'required', 0.5)
    corpus = (foreign, outside, allowed)
    def eligible(row):
        return row.binding == binding and row.pdf_page in filters.pdf_pages and row.section_id in filters.section_ids
    good = tuple(sorted((r for r in corpus if eligible(r)), key=lambda r: (r.cosine_distance, r.chunk_id))[:1])
    bad = tuple(r for r in sorted(corpus, key=lambda r: r.cosine_distance)[:1] if eligible(r))
    assert [r.hit.chunk_id for r in validate_remote_hits(binding, filters, good, 1)] == ['required']
    # 사후 검사만으로는 빈 결과를 잘못된 검색이라고 판단할 수 없다. adapter 회수 oracle도 필요하다.
    assert validate_remote_hits(binding, filters, bad, 1) == ()
    assert good != bad


@pytest.mark.parametrize('kwargs', [dict(pdf_pages=(True,)), dict(pdf_pages=(6, 6)),
                                    dict(section_ids=('s4', 's4')), dict(strict_pages=1)])
def test_filters_do_not_coerce_invalid_inputs(kwargs):
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_FILTER_INVALID'):
        RemoteSearchFilters(**kwargs)


def test_empty_scope_is_not_allowed(binding):
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_BINDING_INVALID'):
        replace(binding, owner_user_id='')
