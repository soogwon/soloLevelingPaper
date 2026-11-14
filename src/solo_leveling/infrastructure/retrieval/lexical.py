"""질문의 핵심 용어로 청크를 다시 찾는 가벼운 어휘 검색(#284).

번역문 임베딩만 쓰면 "Multi-head attention"처럼 질문에 그대로 들어간 영어 용어가
원문에만 있는 청크를 놓칠 수 있다. 같은 논문 안의 청크 수십~수백 개만 다루므로
별도 색인 없이 조회 시점에 BM25 점수를 계산한다.

- 영어·숫자: 소문자 단어. 하이픈 단어는 나눈 단어와 붙인 단어를 함께 쓴다(multi-head → multi, head, multihead).
- 한글: 조사·어미 분리 없이도 맞도록 음절 2-gram을 쓴다.
"""

from __future__ import annotations

from collections import Counter
import math
import re
from typing import Iterable, Mapping, Sequence


_WORD = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_HANGUL = re.compile(r'[가-힣]+')
# 질문에 흔히 붙는 기능어. 논문 고유 용어가 아니어서 점수를 흐린다.
_STOPWORDS = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'do', 'does', 'for', 'from', 'how', 'in',
    'is', 'it', 'of', 'on', 'or', 'that', 'the', 'this', 'to', 'what', 'when', 'why', 'with',
})

BM25_K1 = 1.2
BM25_B = 0.75
RRF_K = 60


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens: list[str] = []
    for word in _WORD.findall(lowered):
        parts = word.split('-')
        tokens.extend(part for part in parts if part not in _STOPWORDS)
        if len(parts) > 1:
            tokens.append(''.join(parts))
    for run in _HANGUL.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def bm25_scores(query: str, documents: Mapping[str, str]) -> dict[str, float]:
    """질문 용어가 하나라도 있는 문서만 양수 점수로 돌려준다."""
    terms = set(tokenize(query))
    if not terms or not documents:
        return {}
    tokenized = {key: Counter(tokenize(text)) for key, text in documents.items()}
    lengths = {key: sum(counts.values()) for key, counts in tokenized.items()}
    average = sum(lengths.values()) / len(lengths) or 1.0
    total = len(tokenized)
    scores: dict[str, float] = {}
    for term in terms:
        frequency = sum(1 for counts in tokenized.values() if term in counts)
        if not frequency:
            continue
        idf = math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
        for key, counts in tokenized.items():
            count = counts.get(term, 0)
            if count:
                norm = count + BM25_K1 * (1 - BM25_B + BM25_B * lengths[key] / average)
                scores[key] = scores.get(key, 0.0) + idf * count * (BM25_K1 + 1) / norm
    return scores


def reciprocal_rank_fusion(rankings: Iterable[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    """여러 순위를 점수 크기와 무관하게 합친다. 순위 목록에 없는 항목은 그 목록에서 점수를 받지 않는다."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return fused
