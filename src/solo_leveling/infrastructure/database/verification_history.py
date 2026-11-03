"""의미 검증 이력 저장소(#83). 스키마는 schema.py의 verification_runs·verification_claims·verification_claim_refs.

- 저장은 짧은 쓰기 트랜잭션 하나(BEGIN IMMEDIATE)다. 실행·주장·참조 중 하나라도 실패하면 모두 되돌린다.
- 계약 결과는 예외 대신 HistoryResult의 outcome으로 돌려준다. 형식 오류만 HistoryValidationError.
- 저장 직전에 같은 트랜잭션에서 확인한다: 맥락이 있고 버전·리비전·색인이 맥락과 같은지, 참조한 청크가 그 맥락의
  파싱·번역 리비전 범위에 속하는지, 청크의 현재 원문 해시가 기록한 해시와 같은지.
- 이력 저장 실패가 답변·최종 근거를 되돌리지는 않는다(호출자가 최종 근거 저장 뒤에 부른다).
- 주장 문구·청크 내용은 로그에 남기지 않는다. 실패 이유는 고정 코드로만 알린다.
"""
import sqlite3
from contextlib import closing
from typing import List, Optional

from solo_leveling.domain.models import now_iso
from solo_leveling.domain.verification_history import (
    HistoryClaim, HistoryOutcome, HistoryReference, HistoryResult, VerificationHistoryRecord, chunk_content_hash,
    validate_record)
from solo_leveling.infrastructure.database.schema import get_connection


def save_verification_history(db_path: str, record: VerificationHistoryRecord) -> HistoryResult:
    """검증 실행 하나의 이력을 한 트랜잭션으로 저장한다. 같은 run_id가 있으면 다시 저장하지 않는다."""
    validate_record(record)
    try:
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM verification_runs WHERE run_id=?', (record.run_id,)).fetchone():
                return HistoryResult(HistoryOutcome.DUPLICATE_RUN, record.run_id)
            context = conn.execute('SELECT * FROM learning_contexts WHERE context_id=?',
                                   (record.context_id,)).fetchone()
            if context is None or any(context[key] != getattr(record, key) for key in (
                    'version_id', 'parse_revision_id', 'translation_revision_id', 'embedding_set_id')):
                return HistoryResult(HistoryOutcome.REFERENCE_MISMATCH)
            for claim in record.claims:
                for ref in claim.references:
                    chunk = conn.execute(
                        'SELECT original_text, parse_revision_id, translation_revision_id, text FROM chunks '
                        'WHERE chunk_id=?', (ref.chunk_id,)).fetchone()
                    if (chunk is None or chunk['parse_revision_id'] != context['parse_revision_id']
                            or chunk['translation_revision_id'] != context['translation_revision_id']
                            or chunk['text'] is None
                            or chunk_content_hash(chunk['original_text']) != ref.content_hash):
                        return HistoryResult(HistoryOutcome.REFERENCE_MISMATCH)
            conn.execute('INSERT INTO verification_runs (run_id, context_id, version_id, parse_revision_id, '
                         'translation_revision_id, embedding_set_id, model, instruction_version, elapsed_ms, '
                         'level, failure_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         (record.run_id, record.context_id, record.version_id, record.parse_revision_id,
                          record.translation_revision_id, record.embedding_set_id, record.model,
                          record.instruction_version, record.elapsed_ms, record.level, record.failure, now_iso()))
            for position, claim in enumerate(record.claims):
                conn.execute('INSERT INTO verification_claims (run_id, position, claim_id, claim_text, verdict, '
                             'reason, retained) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (record.run_id, position, claim.claim_id, claim.text, claim.verdict, claim.reason,
                              1 if claim.retained else 0))
                for ref_position, ref in enumerate(claim.references):
                    conn.execute('INSERT INTO verification_claim_refs (run_id, position, ref_position, chunk_id, '
                                 'content_hash) VALUES (?, ?, ?, ?, ?)',
                                 (record.run_id, position, ref_position, ref.chunk_id, ref.content_hash))
            return HistoryResult(HistoryOutcome.SAVED, record.run_id)
    except sqlite3.Error:
        return HistoryResult(HistoryOutcome.SAVE_FAILED)  # 세부 내용은 남기지 않는다. with 블록이 되돌렸다.


def get_verification_history(db_path: str, context_id: str, run_id: str) -> Optional[VerificationHistoryRecord]:
    """그 맥락의 실행 이력 하나. 없거나 다른 맥락의 실행이면 None."""
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')
        return _load(conn, context_id, run_id)


def list_verification_history(db_path: str, context_id: str) -> List[VerificationHistoryRecord]:
    """한 맥락의 실행 이력을 만든 순서로 돌려준다(한 번의 읽기 스냅샷)."""
    with closing(get_connection(db_path)) as conn:
        conn.execute('BEGIN')
        ids = [r['run_id'] for r in conn.execute(
            'SELECT run_id FROM verification_runs WHERE context_id=? ORDER BY created_at, rowid', (context_id,))]
        return [r for r in (_load(conn, context_id, run_id) for run_id in ids) if r is not None]


def delete_context_verification_history(db_path: str, context_id: str) -> int:
    """맥락을 지우기 전에 그 맥락의 이력만 한 트랜잭션으로 지운다. 지운 실행 수를 돌려준다."""
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        return conn.execute('DELETE FROM verification_runs WHERE context_id=?', (context_id,)).rowcount


def _load(conn, context_id: str, run_id: str) -> Optional[VerificationHistoryRecord]:
    run = conn.execute('SELECT * FROM verification_runs WHERE run_id=? AND context_id=?',
                       (run_id, context_id)).fetchone()
    if run is None:
        return None
    claims = []
    for row in conn.execute('SELECT * FROM verification_claims WHERE run_id=? ORDER BY position', (run_id,)):
        refs = tuple(HistoryReference(r['chunk_id'], r['content_hash']) for r in conn.execute(
            'SELECT * FROM verification_claim_refs WHERE run_id=? AND position=? ORDER BY ref_position',
            (run_id, row['position'])))
        claims.append(HistoryClaim(row['claim_id'], row['claim_text'], row['verdict'], row['reason'],
                                   bool(row['retained']), refs))
    return VerificationHistoryRecord(
        run['run_id'], run['context_id'], run['version_id'], run['parse_revision_id'],
        run['translation_revision_id'], run['embedding_set_id'], run['model'], run['instruction_version'],
        run['elapsed_ms'], run['level'], run['failure_code'], tuple(claims), run['created_at'])
