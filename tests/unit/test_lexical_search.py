"""#284: 질문의 핵심 용어로 청크를 다시 찾는 어휘 점수."""

from solo_leveling.infrastructure.retrieval.lexical import (
    bm25_scores, reciprocal_rank_fusion, tokenize,
)


def test_tokenize_splits_hyphen_words_and_uses_hangul_bigrams():
    tokens = tokenize('Multi-head attention은 왜 여러 개의 head를 쓰나요?')
    assert {'multi', 'head', 'multihead', 'attention'} <= set(tokens)
    assert '여러' in tokens and '쓰나' in tokens
    assert 'the' not in tokenize('the head')


def test_bm25_prefers_document_with_rare_question_terms():
    documents = {
        'subspaces': 'Multi-head attention allows the model to attend to different representation subspaces.',
        'caption': 'Figure 2: Scaled Dot-Product Attention.',
        'training': 'We trained on the WMT 2014 dataset.',
    }
    scores = bm25_scores('Multi-head attention은 왜 여러 head를 쓰나요?', documents)
    assert max(scores, key=scores.get) == 'subspaces'
    assert 'training' not in scores


def test_bm25_without_shared_terms_returns_nothing():
    assert bm25_scores('질문', {'a': 'Attention.'}) == {}
    assert bm25_scores('   ', {'a': 'Attention.'}) == {}


def test_rank_fusion_rewards_items_found_by_both_rankings():
    fused = reciprocal_rank_fusion([['a', 'b', 'c'], ['c']])
    assert sorted(fused, key=lambda key: -fused[key]) == ['c', 'a', 'b']


def test_original_term_survives_long_translation_and_vector_noise():
    from types import SimpleNamespace
    from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever

    chunks = {
        f'noise-{i}': SimpleNamespace(original_text='Multi-head attention overview and implementation details.', text='어텐션 소개')
        for i in range(8)
    }
    chunks['target'] = SimpleNamespace(
        original_text='Multi-head attention: multiple head subspaces in multi-head attention.',
        text='서로 다른 표현 공간을 활용합니다. ' * 200,
    )
    ranked = [(key, 1.0 / (i + 1)) for i, key in enumerate(chunks)]
    result = SQLiteChromaRetriever._fuse_lexical(
        'Multi-head attention은 왜 여러 head를 쓰나요?', ranked, chunks)
    assert 'target' in [key for key, _ in result[:5]]
    assert len({key for key, _ in result}) == len(chunks)
    chunks['target'].text = '서로 다른 표현 공간을 활용합니다.'
    assert SQLiteChromaRetriever._fuse_lexical(
        'Multi-head attention은 왜 여러 head를 쓰나요?', ranked, chunks) == result


def test_same_terms_in_both_languages_are_not_counted_twice():
    from types import SimpleNamespace
    from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever

    chunks = {
        'target': SimpleNamespace(original_text='Multi-head attention.', text='번역문'),
        'other': SimpleNamespace(original_text='Training details.', text='훈련 설명'),
    }
    ranked = [('other', 0.9), ('target', 0.5)]
    original_only = SQLiteChromaRetriever._fuse_lexical('multi-head', ranked, chunks)
    chunks['target'].text = chunks['target'].original_text
    both = SQLiteChromaRetriever._fuse_lexical('multi-head', ranked, chunks)
    assert both == original_only


def test_no_lexical_overlap_preserves_vector_scores_and_order():
    from types import SimpleNamespace
    from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever

    chunks = {'a': SimpleNamespace(original_text='Attention.', text='어텐션 설명')}
    ranked = [('a', 0.42)]
    assert SQLiteChromaRetriever._fuse_lexical('xyzunknown', ranked, chunks) == ranked
