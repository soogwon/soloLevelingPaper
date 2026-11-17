"""두 소유자의 승인 작업을 실제 큐·게시 DB로 처리한다. R2·처리 단계는 합성 대역이다."""
# ruff: noqa: F811
import os
from threading import Event
from types import SimpleNamespace as NS
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,new_id  # noqa: F401
from tests.integration.test_remote_processing_intake_postgres import admitted_source
from tests.fixtures.processing_result import result,LIMITS,SETTINGS,SPEC
from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake
from solo_leveling.interfaces.http.remote_upload_consents import NOTICE_VERSION
from solo_leveling.interfaces.http.object_storage import DownloadedObject
from solo_leveling.workers.processing_queue import run_processing_queue
import solo_leveling.workers.processing_composition as composition

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


def test_bounded_queue_restart_publishes_each_owner_once(pool,db,monkeypatch):
    jobs=[]
    for _ in range(2):
        owner,intent,storage,ai=admitted_source(pool,db)
        admitted=PostgresRemoteProcessingIntake(pool).admit(owner,intent,new_id(),storage,ai,NOTICE_VERSION)
        jobs.append((owner,admitted.job_id))
    monkeypatch.setattr(composition,'ProcessingStagesFactory',lambda *_args,**_kwargs:
        lambda source,check:NS(run=lambda current,body:result(current)))
    class Objects:
        calls=0
        def read_object(self,key,*,expected_size):
            self.calls+=1
            return DownloadedObject(key,expected_size,'application/pdf','a'*64,True,b'%PDF-test!')
    objects=Objects()
    for worker_id in ('first-process','restarted-process'):
        worker=composition.create_processing_worker(pool,objects,worker_id=worker_id,
            lease_seconds=60,max_staging_bytes=100000,limits=LIMITS,
            service_config=NS(translation_settings=SETTINGS),embedding_spec=SPEC)
        outcome=run_processing_queue(worker,Event(),max_jobs=1)
        assert outcome.status=='limit_reached' and outcome.processed==1
    assert objects.calls==2
    for owner,job_id in jobs:
        assert db('SELECT status FROM server_processing_jobs WHERE owner_user_id=%s AND job_id=%s',(owner,job_id))[0]['status']=='succeeded'
        rows=db('SELECT owner_user_id FROM server_processing_publications WHERE source_job_id=%s',(job_id,))
        assert rows==[{'owner_user_id':owner}]
    assert not db('SELECT attempt_id FROM server_processing_provider_attempts')
