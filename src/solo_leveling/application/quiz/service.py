"""근거 기반 단계별 퀴즈 생성(M1-09, #54).

학습 단계(읽기 가이드의 단계 범위)에 해당하는 번역 청크를 근거로 묶어 생성기에 넘기고,
정답/채점 기준과 확인 가능한 근거를 가진 문항만 저장한다.
생성기가 만든 문항은 신뢰하지 않는다. 형식이 틀렸거나, 정답이 선택지와 어긋나거나,
넘기지 않은 근거를 인용한 문항은 버린다.
"""
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence
from uuid import uuid4

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.reading_guide.service import ReadingGuideService
from solo_leveling.domain.evidence_qa import EvidenceInput
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.models import Chunk, Evidence
from solo_leveling.domain.quiz import Quiz, QuizQuestion, QuizValidationError, split_supported
from solo_leveling.infrastructure.database import repository as repo

MAX_CHUNKS_PER_CALL = 20
MAX_QUESTIONS_PER_QUIZ = 8


@dataclass(frozen=True)
class DraftQuestion:
    """생성기가 돌려주는 검증 전 문항."""
    question_type: str
    prompt: str
    evidence_ids: tuple
    choices: tuple = ()
    correct_index: Optional[int] = None
    answer: str = ''
    grading_criteria: str = ''


class QuizGenerator(Protocol):
    def generate_quiz(self, step: str, evidence: Sequence[EvidenceInput]) -> Sequence[DraftQuestion]:
        """주어진 근거만으로 해당 단계의 문항 초안을 만든다. 검증은 서비스가 한다."""
        ...


class NoSupportedQuiz(Exception):
    """정답과 근거가 확인되는 문항이 하나도 없어 아무것도 저장하지 않았다."""


@dataclass(frozen=True)
class QuizResult:
    quiz: Quiz
    dropped_count: int  # 형식 오류·정답 불일치·근거 미확인·중복으로 버린 문항 수


def _to_question(draft: DraftQuestion) -> QuizQuestion:
    question = QuizQuestion(draft.question_type, draft.prompt, tuple(draft.evidence_ids),
                            tuple(draft.choices), draft.correct_index, draft.answer,
                            draft.grading_criteria)
    # 객관식 정답은 선택지에서 정해진다. 생성기가 따로 적은 정답이 어긋나면 키를 믿을 수 없다.
    if (draft.question_type == 'multiple_choice' and isinstance(draft.answer, str)
            and draft.answer.strip() and draft.answer.strip() != question.answer.strip()):
        raise QuizValidationError('정답 문구가 선택지와 일치하지 않습니다.')
    return question


class QuizService:
    def __init__(self, db_path: str, generator: QuizGenerator,
                 max_chunks_per_call: int = MAX_CHUNKS_PER_CALL,
                 max_questions: int = MAX_QUESTIONS_PER_QUIZ,
                 id_factory: Callable[[], str] = lambda: str(uuid4())):
        if max_chunks_per_call < 1 or max_questions < 1:
            raise ValueError('max_chunks_per_call and max_questions must be positive')
        self.db_path = db_path
        self.generator = generator
        self.max_chunks = max_chunks_per_call
        self.max_questions = max_questions
        self.new_id = id_factory

    def generate(self, context_id: str, step: str) -> QuizResult:
        if step not in LEARNING_STEPS:
            raise QuizValidationError(f'지원하지 않는 학습 단계: {step!r}')
        context = repo.get_learning_context(self.db_path, context_id)
        if context is None:
            raise ResourceNotFoundError('context not found')
        chunks = self._step_chunks(context, step)
        if not chunks:
            raise QuizValidationError('이 단계에 해당하는 번역된 본문이 없습니다.')

        kept, evidences, dropped, seen = [], {}, 0, set()
        for batch in self._batches(chunks):
            if len(kept) >= self.max_questions:
                break  # 이미 충분하면 더 호출하지 않는다.
            inputs, records = [], {}
            for chunk in batch:
                evidence_id = self.new_id()
                inputs.append(EvidenceInput(evidence_id, chunk.chunk_id, chunk.text, chunk.original_text,
                                            chunk.printed_page_label, chunk.pdf_page))
                records[evidence_id] = Evidence(evidence_id, chunk.chunk_id, chunk.text, chunk.original_text)
            valid = []
            for draft in self.generator.generate_quiz(step, inputs):
                try:
                    valid.append(_to_question(draft))
                except (QuizValidationError, TypeError):
                    dropped += 1
            supported, unsupported = split_supported(valid, records)
            dropped += len(unsupported)
            for question in supported:
                key = ' '.join(question.prompt.split()).lower()
                if key in seen:
                    dropped += 1
                    continue
                if len(kept) >= self.max_questions:
                    break
                seen.add(key)
                kept.append(question)
                for evidence_id in question.evidence_ids:
                    evidences[evidence_id] = records[evidence_id]
        if not kept:
            raise NoSupportedQuiz('정답과 근거가 확인되는 문항이 없습니다.')
        repo.save_evidences(self.db_path, context_id, list(evidences.values()))
        quiz = repo.save_quiz(self.db_path, context_id, step, kept)
        return QuizResult(quiz, dropped)

    def _step_chunks(self, context, step: str):
        """읽기 가이드가 이 단계에 배정한 섹션(없으면 페이지 범위)의 번역 청크."""
        if not context.translation_revision_id:
            return []
        guide = ReadingGuideService(self.db_path).get_guide(context.context_id)
        scope = next(s for s in guide.steps if s.step == step)
        chunks = repo.get_translated_chunks(
            self.db_path, context.parse_revision_id, context.translation_revision_id)
        if scope.section_ids:
            return [c for c in chunks if c.section_id in scope.section_ids]
        if scope.pdf_pages:
            first, last = scope.pdf_pages
            return [c for c in chunks if first <= c.pdf_page <= last]
        return []

    def _batches(self, chunks: Sequence[Chunk]):
        """같은 섹션끼리 묶고, 한 번의 호출이 max_chunks를 넘지 않게 나눈다."""
        group, section = [], None
        for chunk in chunks:
            if group and chunk.section_id != section:
                yield from self._split(group)
                group = []
            section = chunk.section_id
            group.append(chunk)
        if group:
            yield from self._split(group)

    def _split(self, group):
        for start in range(0, len(group), self.max_chunks):
            yield group[start:start + self.max_chunks]
