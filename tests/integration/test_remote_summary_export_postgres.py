"""같은 고정 결과의 내보내기·계정 격리·삭제 차단을 확인한다."""
# ruff: noqa: F811
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_summary_intake_postgres import setup,submit,completed_attempt
from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
from solo_leveling.application.summary.remote_export import export_summary
pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def test_export_fixed_result_without_new_generation(pool,db,stores):
    args=setup(db,stores,pool);key=str(uuid4());row=submit(pool,args,key)
    intent=completed_attempt(pool,args,row);binding,sources,_=args[0].unpack()
    service=PostgresSummaryResults(pool)
    service.complete(intent,[dict(kind='method',text='<script> ![x](https://example.com) 합성 요약',evidence_ids=[sources[0].evidence_id])])
    saved=service.latest('alice',binding.context_id);exported=export_summary(saved)
    assert exported['request_key']==key and exported['publication_id']==binding.publication_id
    assert binding.server_version_id in exported['markdown'] and sources[0].evidence_id in exported['markdown']
    assert '<script>' not in exported['markdown'] and '![x]' not in exported['markdown']
    assert export_summary(service.get('alice',binding.context_id,key))==exported
    assert service.latest('bob',binding.context_id) is None
    assert len(db('SELECT * FROM server_provider_attempts WHERE execution_id=%s',(row.execution_id,)))==1
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(binding.paper_id,))
    assert service.latest('alice',binding.context_id) is None
