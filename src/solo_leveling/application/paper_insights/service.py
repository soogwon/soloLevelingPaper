"""명시적인 미리보기·저장만 수행하는 오프라인 추출 서비스."""
from collections import Counter
from dataclasses import dataclass
import re
from typing import Protocol

from solo_leveling.application.evidence_qa.fragments import fragment_flags, sentence_ends
from solo_leveling.application.evidence_qa.errors import DataIntegrityError
from solo_leveling.domain.paper_insight import (
    CONTENT_EXTRACTED, KIND_IMPORTANT_SENTENCE, KIND_KEYWORD, PRODUCER_EXTRACTOR,
    PaperInsightCollection, PaperInsightConflictError, PaperInsightInput, PaperInsightValidationError,
)

_WORDS = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", re.UNICODE)
_STOP = frozenset('''the and for that this with from are was were has have had not but
    into can may will would could should their these those its our also than then
    which when where what how such each other both between through been being
    using used use does did only more most some all any they them there here'''.split())
_EXCLUDED_SECTIONS = frozenset({'references', 'bibliography', 'acknowledgments', 'acknowledgements', '참고문헌'})


@dataclass(frozen=True)
class InsightChunk:
    chunk_id: str
    parse_revision_id: str
    chunk_index: int
    original_text: str
    pdf_page: int
    section_id: str | None = None


@dataclass(frozen=True)
class InsightSource:
    context_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str
    embedding_set_id: str
    scope: str
    chunks: tuple[InsightChunk, ...]
    section_titles: tuple[tuple[str, str], ...] = ()
    existing_collection_id: str | None = None


class InsightStore(Protocol):
    def load(self, context_id: str, scope: str) -> InsightSource: ...
    def get(self, context_id: str, scope: str) -> PaperInsightCollection | None: ...
    def save(self, source: InsightSource, items: tuple[PaperInsightInput, ...], *,
             expected_collection_id: str | None) -> PaperInsightCollection: ...


@dataclass(frozen=True)
class ExtractionPolicy:
    max_keywords: int = 8
    max_sentences: int = 5
    min_sentence_frequency: int = 2

    def __post_init__(self):
        for value, upper in ((self.max_keywords, 20), (self.max_sentences, 10), (self.min_sentence_frequency, 10)):
            if type(value) is not int or not 1 <= value <= upper:
                raise PaperInsightValidationError('추출 설정 범위가 올바르지 않습니다.')

    @property
    def version(self):
        return f'extractive-v1:k{self.max_keywords}:s{self.max_sentences}:f{self.min_sentence_frequency}'


@dataclass(frozen=True)
class InsightPreview:
    source: InsightSource
    items: tuple[PaperInsightInput, ...]
    producer_version: str

    @property
    def status(self):
        kinds = {item.kind for item in self.items}
        return 'available' if len(kinds) == 2 else 'partial' if kinds else 'not_available'


def _terms(text):
    return tuple(match for match in _WORDS.finditer(text)
                 if len(match.group()) >= 3 and match.group().casefold() not in _STOP)


def _sentences(source):
    """저장 문자열의 오프셋을 유지한다. 경계 판정은 형식 추정이며 의미 보증이 아니다."""
    titles = dict(source.section_titles)
    seen = set()
    for chunk in source.chunks:
        title = re.sub(r'^\s*\d+(?:\.\d+)*\.?\s+', '', titles.get(chunk.section_id, '')).strip().casefold()
        if title in _EXCLUDED_SECTIONS:
            continue
        text, start = chunk.original_text, 0
        for end in sentence_ends(text):
            left, right = start, end
            start = end
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1
            sentence = text[left:right]
            if not sentence or not sentence[0].isalpha() or sentence[0].islower() or any(fragment_flags(sentence)):
                continue
            if any(line.strip().isdigit() for line in sentence.splitlines()):
                continue
            if not 30 <= len(sentence) <= 800 or len(_WORDS.findall(sentence)) < 5:
                continue
            key = ' '.join(sentence.split()).casefold()
            if key in seen:
                continue
            seen.add(key)
            yield chunk, left, right, sentence


class PaperInsightService:
    def __init__(self, store: InsightStore, policy: ExtractionPolicy | None = None):
        self.store = store
        self.policy = policy or ExtractionPolicy()

    def preview(self, context_id: str, scope: str = 'paper') -> InsightPreview:
        if any(not isinstance(value, str) or not value.strip() for value in (context_id, scope)):
            raise PaperInsightValidationError('학습 맥락과 범위가 필요합니다.')
        source = self.store.load(context_id, scope)
        return self._select(source)

    def _select(self, source: InsightSource) -> InsightPreview:
        sentences = tuple(_sentences(source))
        terms = [dict.fromkeys(match.group().casefold() for match in _terms(row[3])) for row in sentences]
        counts = Counter(term for sentence in terms for term in sentence)
        first = {}
        for position, (chunk, start, _, text) in enumerate(sentences):
            for match in _terms(text):
                first.setdefault(match.group().casefold(), (position, chunk, start + match.start(), start + match.end(), match.group()))
        keywords = sorted((key for key, count in counts.items() if count >= self.policy.min_sentence_frequency),
                          key=lambda key: (-counts[key], first[key][0], first[key][2], key))[:self.policy.max_keywords]
        items = []
        for key in keywords:
            _, chunk, start, end, text = first[key]
            items.append(PaperInsightInput(KIND_KEYWORD, text, CONTENT_EXTRACTED, PRODUCER_EXTRACTOR,
                chunk.chunk_id, start, end, text, producer_version=self.policy.version))
        ranking = sorted(range(len(sentences)), key=lambda i: (-sum(counts[key] for key in terms[i]), i))
        for i in ranking[:self.policy.max_sentences]:
            chunk, start, end, text = sentences[i]
            items.append(PaperInsightInput(KIND_IMPORTANT_SENTENCE, text, CONTENT_EXTRACTED, PRODUCER_EXTRACTOR,
                chunk.chunk_id, start, end, text, producer_version=self.policy.version))
        return InsightPreview(source, tuple(items), self.policy.version)

    def save(self, preview: InsightPreview, *, replace_existing: bool = False) -> PaperInsightCollection:
        """명시 저장. 비어 있는 결과는 저장하지 않으며 기존 묶음 교체는 별도 지정한다."""
        if (not isinstance(preview, InsightPreview) or not isinstance(preview.source, InsightSource)
                or preview.producer_version != self.policy.version):
            raise PaperInsightValidationError('미리보기의 추출 설정이 일치하지 않습니다.')
        if type(replace_existing) is not bool:
            raise PaperInsightValidationError('교체 여부는 bool이어야 합니다.')
        if not preview.items:
            raise PaperInsightValidationError('저장할 추출 항목이 없습니다.')
        if any(not isinstance(item, PaperInsightInput) or item.content_kind != CONTENT_EXTRACTED or item.producer != PRODUCER_EXTRACTOR
               or item.producer_version != preview.producer_version for item in preview.items):
            raise PaperInsightValidationError('추출 항목의 작성 설정이 일치하지 않습니다.')
        if self._select(preview.source) != preview:
            raise PaperInsightValidationError('미리보기 항목이 추출 규칙의 결과와 다릅니다.')
        current = self.store.load(preview.source.context_id, preview.source.scope)
        if current.existing_collection_id != preview.source.existing_collection_id:
            raise PaperInsightConflictError('미리보기 이후 항목 묶음이 변경되었습니다.')
        if current != preview.source:
            raise DataIntegrityError('미리보기의 원문 또는 학습 맥락이 변경되었습니다.')
        existing = self.store.get(preview.source.context_id, preview.source.scope)
        if (existing.collection_id if existing else None) != preview.source.existing_collection_id:
            raise PaperInsightConflictError('미리보기 이후 항목 묶음이 변경되었습니다.')
        if existing is not None and not replace_existing:
            raise PaperInsightValidationError('기존 결과 교체에는 명시적인 지정이 필요합니다.')
        return self.store.save(preview.source, preview.items,
            expected_collection_id=preview.source.existing_collection_id)
