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


def test_demo_queue_publishes_eligible_owners_and_pauses_intake(pool, db, monkeypatch):
    from tests.unit.test_demo_processing import active_settings
    from solo_leveling.application.provider_dispatch import DispatchError, ProviderDispatch, ProviderResponse
    from solo_leveling.interfaces.cli.processing_intake_stop import pause_intake
    from solo_leveling.workers.demo_processing import DemoBudget, DemoJobs

    intake = PostgresRemoteProcessingIntake(pool)
    inputs = [admitted_source(pool, db) for _ in range(3)]
    paused_owner, paused_intent, paused_storage, paused_ai = admitted_source(pool, db)
    admitted = [
        intake.admit(owner, intent, new_id(), storage, ai, NOTICE_VERSION)
        for owner, intent, storage, ai in inputs
    ]
    for scope, identifier in [('service','service'), ('owner', inputs[0][0]), ('owner', inputs[1][0])]:
        db("""INSERT INTO server_dispatch_budget_limits(
                  scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
              VALUES (%s,%s,'demo','window',200,100)""", (scope, identifier))
    controls = db("SELECT generation FROM server_dispatch_controls WHERE control_id='provider_dispatch'")
    with pool.connection() as conn:
        generation = controls[0]['generation']
        assert pause_intake(conn, generation)
        assert pause_intake(conn, generation) is None
    assert intake.admit(paused_owner, paused_intent, new_id(), paused_storage, paused_ai, NOTICE_VERSION) is None
    assert intake.admit(*inputs[-1][:2], new_id(), *inputs[-1][2:], NOTICE_VERSION).job_id == admitted[-1].job_id
    budget = DemoBudget(active_settings(), 'fixed-model', 50)
    calls = []

    def stages(authorization, **_):
        def prepare(source, check):
            ledger = authorization.ledger(source, check, policy_version='demo', window_key='window',
                                          validate_limits=budget.allows_limits)
            dispatch = ProviderDispatch(ledger, authorization.dispatch_intent(source))

            def transport(request):
                calls.append(request.intent.owner_user_id)
                return ProviderResponse({}, 100, 50)

            def process(current, _body):
                dispatch.send('translation', 100, 50, transport, dispatch_key='one')
                with pytest.raises(DispatchError):
                    dispatch.send('translation', 101, 51, transport, dispatch_key='over-budget')
                return result(current)

            return NS(run=process)

        return prepare

    monkeypatch.setattr(composition, 'ProcessingStagesFactory', stages)

    class Objects:
        def read_object(self, key, *, expected_size):
            return DownloadedObject(key, expected_size, 'application/pdf', 'a' * 64, True, b'%PDF-test!')

    jobs = DemoJobs(pool, budget, 'demo', 'window')
    worker = composition.create_processing_worker(
        pool, Objects(), worker_id='demo-test', jobs=jobs, lease_seconds=60, max_staging_bytes=100000,
        limits=LIMITS, service_config=NS(translation_settings=SETTINGS), embedding_spec=SPEC,
    )
    assert run_processing_queue(worker, Event(), max_jobs=2, settle_failed=jobs.settle_failed).processed == 2
    assert jobs.claim('demo-test') is None
    for job, status in zip(admitted, ('succeeded','succeeded','queued'), strict=True):
        assert db('SELECT status FROM server_processing_jobs WHERE job_id=%s',(job.job_id,)) == [{'status':status}]
    assert set(calls) == {inputs[0][0], inputs[1][0]} and len(calls) == 2
    assert db('''SELECT reserved_input_tokens,consumed_input_tokens,consumed_output_tokens
                 FROM server_dispatch_budget_limits
                 WHERE scope_type='service' AND policy_version='demo' AND window_key='window' ''') == [
        {'reserved_input_tokens':0, 'consumed_input_tokens':200, 'consumed_output_tokens':100},
    ]
