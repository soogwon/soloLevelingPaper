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
