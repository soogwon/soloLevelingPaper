"""단계별 읽기 가이드(M1-06, #51).

저장된 청크·섹션과 학습 진행 상태로 가이드를 조립한다. AI를 호출하지 않는다.
핵심 개념은 근거 연결 요약(M1-05)에서 가져오며, 요약이 없으면 비워 둔다.
"""
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Sequence

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.reading_guide.summary_concepts import SummaryKeyConceptSource
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.reading_guide import (
    GOAL_HINTS_KO, STEP_GOALS_KO, STEP_TITLES_KO, ReadingGuide, StepGuide,
)
from solo_leveling.infrastructure.database import repository as repo

# 섹션 id는 'NN-slug' 형태다. slug에 아래 단어가 들어 있으면 해당 용도의 섹션으로 본다.
_PREVIEW = ('abstract', 'introduction', 'conclusion')
_SKIP_IN_DEEP_READ = ('abstract', 'references', 'bibliography', 'acknowledg')
_REVIEW = ('result', 'experiment', 'evaluation', 'discussion', 'limitation', 'conclusion')
FALLBACK_PREVIEW_PAGES = 2


class KeyConceptSource(Protocol):
    def key_concepts(self, context_id: str) -> Mapping[str, Sequence[str]]:
        """단계 이름 -> 핵심 개념 문구 목록. 없는 단계는 생략해도 된다."""
        ...


@dataclass(frozen=True)
class _Section:
    section_id: str
    first_page: int
    last_page: int


def _slug(section_id: str) -> str:
    return re.sub(r'^\d+-', '', section_id).lower()


def _matches(section: _Section, words) -> bool:
    slug = _slug(section.section_id)
    return any(word in slug for word in words)


def _pages(sections: Sequence[_Section]):
    if not sections:
        return None
    return (min(s.first_page for s in sections), max(s.last_page for s in sections))


class ReadingGuideService:
    def __init__(self, db_path: str, concept_source: Optional[KeyConceptSource] = None):
        self.db_path = db_path
        # 따로 주지 않으면 저장된 근거 연결형 요약에서 핵심 개념을 가져온다. 요약이 없으면 빈 목록이다.
        self.concept_source = concept_source if concept_source is not None else SummaryKeyConceptSource(db_path)

    # ── 조회 ────────────────────────────────────────────────
    def get_guide(self, context_id: str) -> ReadingGuide:
        """현재 진행 상태가 반영된 단계별 가이드. 멈춘 곳에서 다시 시작할 때도 이것을 쓴다."""
        context = repo.get_learning_context(self.db_path, context_id)
        if context is None:
            raise ResourceNotFoundError('context not found')
        progress = repo.get_learning_progress(self.db_path, context_id)
        chunks = repo.get_chunks_by_parse_revision(self.db_path, context.parse_revision_id)
        sections = self._sections(chunks)
        all_pages = (min(c.pdf_page for c in chunks), max(c.pdf_page for c in chunks)) if chunks else None
        concepts = self.concept_source.key_concepts(context_id)

        steps = []
        for step in LEARNING_STEPS:
            chosen, pages = self._scope(step, sections, all_pages)
            status = ('done' if step in progress.completed_steps
                      else 'current' if step == progress.current_step else 'upcoming')
            goal = STEP_GOALS_KO[step]
            hint = GOAL_HINTS_KO.get(context.goal, {}).get(step)
            steps.append(StepGuide(
                step, STEP_TITLES_KO[step], f'{goal} {hint}' if hint else goal, status,
                tuple(s.section_id for s in chosen), pages,
                tuple(concepts.get(step, ()))))
        return ReadingGuide(
            context_id, context.goal, tuple(steps), progress.current_step,
            tuple(progress.completed_steps), progress.resume_position,
            tuple(context.known_concepts), progress.last_studied_at)

    # ── 단계 시작·완료 ──────────────────────────────────────
    def start_step(self, context_id: str, step: str,
                   resume_position: Optional[dict] = None) -> ReadingGuide:
        repo.set_current_step(self.db_path, context_id, step, resume_position)
        return self.get_guide(context_id)

    def complete_step(self, context_id: str, step: str) -> ReadingGuide:
        repo.complete_step(self.db_path, context_id, step)
        return self.get_guide(context_id)

    # ── 단계별 범위 ─────────────────────────────────────────
    @staticmethod
    def _sections(chunks) -> list:
        """섹션을 처음 나타난 순서대로 모으고 페이지 범위를 계산한다. section_id가 없는 청크는 제외한다."""
        found: dict = {}
        for chunk in chunks:
            if chunk.section_id is None:
                continue
            first, last = found.get(chunk.section_id, (chunk.pdf_page, chunk.pdf_page))
            found[chunk.section_id] = (min(first, chunk.pdf_page), max(last, chunk.pdf_page))
        return [_Section(sid, first, last) for sid, (first, last) in found.items()]

    @staticmethod
    def _scope(step, sections, all_pages):
        if not sections:  # 섹션 구분이 없는 논문은 페이지 범위로 안내한다.
            if all_pages is None:
                return [], None
            if step == 'preview':
                return [], (all_pages[0], min(all_pages[1], all_pages[0] + FALLBACK_PREVIEW_PAGES - 1))
            return [], all_pages
        if step == 'preview':
            chosen = [s for s in sections if _matches(s, _PREVIEW)] or sections[:1]
        elif step == 'structure':
            chosen = list(sections)
        elif step == 'deep_read':
            chosen = [s for s in sections if not _matches(s, _SKIP_IN_DEEP_READ)] or list(sections)
        else:
            chosen = [s for s in sections if _matches(s, _REVIEW)] or sections[-1:]
        return chosen, _pages(chosen)
