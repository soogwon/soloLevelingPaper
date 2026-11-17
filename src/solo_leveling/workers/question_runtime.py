"""질문 실행·정리용 연결 수명. 실행 설정은 검토한 서버 bootstrap에서 주입한다."""
from contextlib import contextmanager
import os
import re

from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from .remote_question import QuestionMaintenanceRunner


@contextmanager
def execution_runtime(pool_factory, **options):
    """전용 DB pool과 검색·제공자 설정을 기존 질문 실행 조립체에 연결한다."""
    from .question_composition import create_question_runner
    pool = pool_factory()
    try:
        yield create_question_runner(pool, **options)
    finally:
        pool.close()


@contextmanager
def maintenance_factory():
    """전용 DB 자격만 사용한다. 모델·API 키를 읽거나 공급자를 생성하지 않는다."""
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    dsn = os.environ.get('QUESTION_WORKER_DATABASE_URL')
    schema = os.environ.get('QUESTION_WORKER_SCHEMA', '')
    if not dsn or not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema):
        raise ValueError('question_worker_database_required')
    pool = ConnectionPool(dsn, open=False, min_size=1, max_size=2, timeout=10,
        kwargs={'row_factory':dict_row,'connect_timeout':5,'options':f'-c search_path={schema}'})
    try:
        pool.open(wait=True)
        yield QuestionMaintenanceRunner(PostgresRemoteExecutionStore(pool))
    finally:
        pool.close()
