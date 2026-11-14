"""학습 맥락의 논문·목적에 맞춰 검수 KG의 학습 경로와 선수 개념을 조회한다."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import UUID

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError, ResourceNotFoundError
from solo_leveling.domain.knowledge_graph import KnowledgeGraph, learning_path
from .views import path_view, prerequisites_view

MODES = ('compact', 'expanded')


class LearningPathService:
    """읽기 전용 서비스. 그래프는 시작 시 메모리에 올린 검수 시드다."""

    def __init__(self, db_path: str, graph: KnowledgeGraph, *, arxiv_reader=None):
        self.db_path = db_path
        self.graph = graph
        if arxiv_reader is None:
            from solo_leveling.infrastructure.parsing.arxiv_stamp import read_arxiv_id
            arxiv_reader = read_arxiv_id
        self.arxiv_reader = arxiv_reader
        self._arxiv_by_hash: dict[str, str | None] = {}

    def _paper(self, file_hash: str, stored_path: str):
        """검수 판본이면 (논문, False), 같은 arXiv 번호의 다른 판본이면 (논문, True)."""
        paper = self.graph.paper_by_hash(file_hash)
        if paper is not None:
            return paper, False
        if file_hash not in self._arxiv_by_hash:
            # 같은 파일은 다시 읽지 않는다. 파일이 없거나 표기가 없으면 None으로 기억한다.
            self._arxiv_by_hash[file_hash] = self.arxiv_reader(stored_path)
        related = self.graph.paper_by_arxiv(self._arxiv_by_hash[file_hash])
        return related, related is not None

    def _context(self, context_id: str) -> tuple[str, list[str], str, str]:
        if not isinstance(context_id, str) or not context_id.strip():
            raise InvalidArgumentError('학습 맥락 ID가 필요합니다.')
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            row = conn.execute('''SELECT l.goal, l.known_concepts, v.file_hash, v.stored_path
                                  FROM learning_contexts l
                                  JOIN paper_versions v ON v.version_id=l.version_id
                                  WHERE l.context_id=?''', (context_id,)).fetchone()
        if row is None:
            try:
                UUID(context_id)
            except ValueError:
                raise InvalidArgumentError('학습 맥락 ID 형식이 올바르지 않습니다.') from None
            raise ResourceNotFoundError('학습 맥락을 찾을 수 없습니다.')
        return row[0], json.loads(row[1]), row[2], row[3]

    def _base(self, context_id: str, paper, related: bool = False) -> dict:
        status = 'no_graph' if paper is None else ('related_version' if related else 'ready')
        return {'context_id': context_id, 'graph_status': status,
                'graph_revision_id': self.graph.revision_id if paper else None,
                'reviewed_version': ({'label': paper.label, 'source_url': paper.source_url}
                                     if related else None)}

    def get_learning_path(self, context_id: str, known_concepts: list[str] | None = None,
                          mode: Literal['compact', 'expanded'] = 'compact') -> dict:
        if mode not in MODES:
            raise InvalidArgumentError('mode는 compact 또는 expanded입니다.')
        goal, stored_known, file_hash, stored_path = self._context(context_id)
        known = stored_known if known_concepts is None else _clean(known_concepts)
        paper, related = self._paper(file_hash, stored_path)
        result = {**self._base(context_id, paper, related), 'goal': goal, 'mode': mode}
        if paper is None:
            return {**result, 'steps': [], 'skipped': [], 'unresolved_concepts': []}
        view = path_view(learning_path(self.graph, paper, goal, known, mode))
        if related:
            # 개념·선수 관계는 판본과 무관하지만, 페이지·찾을 말은 검수 판본 기준이라 숨긴다.
            view['steps'] = [{**step, 'paper_locations': []} for step in view['steps']]
        return {**result, **view}

    def get_prerequisites(self, context_id: str, concept: str) -> dict:
        if not isinstance(concept, str) or not concept.strip():
            raise InvalidArgumentError('개념 이름이 필요합니다.')
        _, _, file_hash, stored_path = self._context(context_id)
        paper, related = self._paper(file_hash, stored_path)
        base = self._base(context_id, paper, related)
        if paper is None:
            return {**base, 'concept': None, 'prerequisites': [], 'required_by': [],
                    'paper_locations': []}
        concept_id = self.graph.resolve(concept)
        if concept_id is None:
            raise ResourceNotFoundError('그래프에 없는 개념입니다.')
        view = prerequisites_view(self.graph, concept_id, paper)
        if related:
            view['paper_locations'] = []
        return {**base, **view}

    def has_graph(self, version_id: str) -> bool:
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            row = conn.execute('SELECT file_hash, stored_path FROM paper_versions WHERE version_id=?',
                               (version_id,)).fetchone()
        # 검수 판본뿐 아니라 같은 논문의 다른 판본도 개념 경로를 볼 수 있다.
        return row is not None and self._paper(row[0], row[1])[0] is not None


def _clean(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise InvalidArgumentError('known_concepts에는 비어 있지 않은 문자열만 넣을 수 있습니다.')
        cleaned.append(value.strip())
    if len(set(cleaned)) != len(cleaned):
        raise InvalidArgumentError('known_concepts에 중복 값이 있습니다.')
    return cleaned
