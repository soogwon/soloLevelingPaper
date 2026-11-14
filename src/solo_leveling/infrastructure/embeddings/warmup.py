"""MCP 서버가 뜨자마자 임베딩 worker를 미리 준비한다(#119).

첫 ask_paper가 라이브러리 import·모델 로딩까지 떠안아 호스트의 60초 대기를 넘기던 문제를 줄인다.
등록된 색인의 모델을 로컬 캐시만으로 준비한다. 캐시가 없으면 다운로드 없이 건너뛴다.
"""

from contextlib import closing
import sqlite3
from threading import Thread

from solo_leveling.diagnostics import traced
from solo_leveling.infrastructure.database.schema import get_connection

# 질문·논문과 무관한 고정 문장이다.
WARMUP_TEXT = 'warm-up'


def default_index_model(db_path: str) -> str | None:
    """가장 최근에 만든 기본 색인의 임베딩 모델. 색인이 없거나 읽지 못하면 None."""
    try:
        with closing(get_connection(db_path)) as conn:
            row = conn.execute('''SELECT e.model_name FROM search_indexes s
                                  JOIN embedding_sets e USING(embedding_set_id)
                                  WHERE s.is_default=1 ORDER BY e.created_at DESC LIMIT 1''').fetchone()
    except sqlite3.Error:
        return None
    return row['model_name'] if row else None


@traced('embedding_warmup', request=True)
def _warm(embedder, model_name):
    try:
        embedder([WARMUP_TEXT], model_name=model_name, local_files_only=True)
    except Exception:
        # 준비 실패는 첫 질문에서 같은 경로로 다시 드러난다. 여기서는 서버 시작을 막지 않는다.
        pass


def start_warmup(embedder, db_path: str) -> Thread | None:
    model_name = default_index_model(db_path)
    if model_name is None:
        return None
    thread = Thread(target=_warm, args=(embedder, model_name), name='embedding-warmup', daemon=True)
    thread.start()
    return thread
