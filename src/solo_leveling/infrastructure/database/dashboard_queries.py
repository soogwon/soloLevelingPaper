"""하나의 읽기 전용 스냅샷에서 학습 맥락의 대시보드를 조회한다."""

from contextlib import closing
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3

from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.paper_summary import PAPER_SCOPE, SummaryItem


class DashboardReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get(self, context_id: str) -> dict:
        if not isinstance(context_id, str) or not context_id.strip():
            raise InvalidArgumentError('학습 맥락 ID가 필요합니다.')
        # 조회 때문에 DB 파일이나 진행 기록을 만들지 않는다. 모든 SELECT가 같은 시점을 본다.
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN')
            context = conn.execute('SELECT * FROM learning_contexts WHERE context_id=?',
                                   (context_id,)).fetchone()
            if context is None:
                raise ResourceNotFoundError('학습 맥락을 찾을 수 없습니다.')
            row = conn.execute('''SELECT p.paper_id, p.title, v.original_filename,
                j.job_id, j.status, j.stage, j.limitations
                FROM search_indexes i
                JOIN paper_versions v ON v.version_id=i.version_id
                JOIN papers p ON p.paper_id=v.paper_id
                JOIN processing_jobs j ON j.job_id=i.job_id AND j.version_id=i.version_id
                WHERE i.embedding_set_id=? AND i.version_id=?
                  AND i.parse_revision_id=? AND i.translation_revision_id=?''',
                tuple(context[key] for key in ('embedding_set_id', 'version_id',
                                               'parse_revision_id', 'translation_revision_id'))).fetchone()
            if row is None:
                raise DataIntegrityError('학습 맥락의 색인 연결이 올바르지 않습니다.')
            return {
                'context_id': context_id, 'version_id': context['version_id'],
                'paper_id': row['paper_id'], 'title': row['title'], 'goal': context['goal'],
                'file_display_name': PureWindowsPath(PurePosixPath(row['original_filename']).name).name,
                'processing': {'job_id': row['job_id'], 'status': row['status'],
                               'stage': row['stage'], 'limitations': json.loads(row['limitations'])},
                'progress': self._progress(conn, context_id),
                'summary': self._summary(conn, context_id),
                'quiz_scores': self._scores(conn, context_id),
            }

    @staticmethod
    def _progress(conn, context_id):
        row = conn.execute('SELECT * FROM learning_progress WHERE context_id=?',
                           (context_id,)).fetchone()
        completed = json.loads(row['completed_steps']) if row else []
        current = row['current_step'] if row else LEARNING_STEPS[0]
        if (not isinstance(completed, list) or any(step not in LEARNING_STEPS for step in completed)
                or len(set(completed)) != len(completed) or current not in LEARNING_STEPS):
            raise DataIntegrityError('저장된 진행 상태가 올바르지 않습니다.')
        return {'recorded': row is not None, 'current_step': current,
                'completed_steps': [step for step in LEARNING_STEPS if step in completed],
                'total_steps': len(LEARNING_STEPS), 'completion_ratio': len(completed) / len(LEARNING_STEPS),
                'last_studied_at': row['last_studied_at'] if row else None}

    @staticmethod
    def _summary(conn, context_id):
        row = conn.execute('SELECT * FROM summaries WHERE context_id=? AND scope=?',
                           (context_id, PAPER_SCOPE)).fetchone()
        if row is None:
            return None
        items = []
        for item in conn.execute('SELECT * FROM summary_items WHERE summary_id=? ORDER BY position',
                                 (row['summary_id'],)):
            validated = SummaryItem(item['kind'], item['text'], json.loads(item['evidence_ids']))
            for evidence_id in validated.evidence_ids:
                if not conn.execute('SELECT 1 FROM evidences WHERE context_id=? AND evidence_id=?',
                                    (context_id, evidence_id)).fetchone():
                    raise DataIntegrityError('요약 근거가 학습 맥락과 일치하지 않습니다.')
            items.append({'kind': validated.kind, 'text': validated.text,
                          'evidence_ids': list(validated.evidence_ids)})
        if not items:
            raise DataIntegrityError('저장된 요약이 비어 있습니다.')
        return {'summary_id': row['summary_id'], 'created_at': row['created_at'], 'items': items}

    @staticmethod
    def _scores(conn, context_id):
        # 채점은 다시 하지 않고 저장된 정답 수·문항 수만 집계한다.
        rows = conn.execute('SELECT * FROM quiz_attempts WHERE context_id=? '
                            'ORDER BY submitted_at, rowid', (context_id,)).fetchall()
        if any(row['step'] not in LEARNING_STEPS or row['total_count'] <= 0
               or not 0 <= row['correct_count'] <= row['total_count'] for row in rows):
            raise DataIntegrityError('저장된 퀴즈 점수가 올바르지 않습니다.')
        scores = []
        for step in LEARNING_STEPS:
            attempts = [row for row in rows if row['step'] == step]
            latest = attempts[-1] if attempts else None
            scores.append({'step': step, 'attempt_count': len(attempts),
                'latest_attempt_id': latest['attempt_id'] if latest else None,
                'latest_correct': latest['correct_count'] if latest else None,
                'latest_total': latest['total_count'] if latest else None,
                'latest_score': latest['correct_count'] / latest['total_count'] if latest else None,
                'best_score': max(row['correct_count'] / row['total_count'] for row in attempts)
                              if attempts else None})
        return scores
