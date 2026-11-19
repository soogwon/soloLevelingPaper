"""복구 실행은 합성 DB·PDF·응답만 사용한다. 외부 전송은 없다."""
# ruff: noqa: F811
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import json
import os
import pytest
from tests.integration.test_remote_reprocessing_postgres import setup, grant
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from solo_leveling.workers.remote_reprocessing import ReprocessingWorker, ReprocessingStages, ReprocessingApproval
from solo_leveling.workers.reprocessing_composition import translation_factory
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationProvider, OpenAITranslationConfig
from solo_leveling.infrastructure.generation.gated_responses import payload_digest
from solo_leveling.domain.translation import TranslationRequest, TranslationSettings

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='로컬 PostgreSQL 필요')


def ready(db,stores,pool,operation='translation_recovery'):
    remote,v,reviews,key=setup(db,stores,pool)
    reviews.contract.update(provider='openai',model='synthetic')
    reviews.prepare(remote.owner,key,v.context,operation);grant(reviews,remote.owner,key);reviews.submit(remote.owner,key)
    row=db('SELECT * FROM server_reprocessing_reviews WHERE request_key=%s',(key,))[0]
    approval=ReprocessingApproval(remote.owner,key,row['input_hash'])
    return remote,v,reviews,approval,row


def row_for(db,a):return db('SELECT * FROM server_reprocessing_reviews WHERE request_key=%s',(a.request_key,))[0]


def test_real_reparse_stage_preserves_old_publication_and_returns_budget(db,stores,pool):
    from tests.fixtures.pdf_builder import build_minimal_pdf
    remote,v,reviews,key=setup(db,stores,pool)
    pdf=build_minimal_pdf(['Synthetic paper. Keep notes after completion.'])
    object_key=f'private/files/{sha256(remote.owner.encode()).hexdigest()[:24]}/{v.paper}/v1/test.pdf'
    db('UPDATE server_pdf_uploads SET sha256_hex=%s,file_size=%s,object_key=%s WHERE owner_user_id=%s',
       (sha256(pdf).hexdigest(),len(pdf),object_key,remote.owner))
    reviews.prepare(remote.owner,key,v.context,'reparse');grant(reviews,remote.owner,key);reviews.submit(remote.owner,key)
    a=ReprocessingApproval(remote.owner,key,db('SELECT input_hash FROM server_reprocessing_reviews WHERE request_key=%s',(key,))[0]['input_hash'])
    reads=[]
    objects=SimpleNamespace(read_object=lambda key,expected_size:reads.append((key,expected_size)) or SimpleNamespace(body=pdf))
    worker=ReprocessingWorker(reviews,a,ReprocessingStages(pool,objects),execution_enabled=True)
    assert worker.run_once()=='staged'
    result=row_for(db,a)['staged_result']
    assert result['chunks'][0]['original_text'].startswith('Synthetic paper')
    assert result['parse_revision_id']!=str(v.parse) and not result['publication_ready']
    assert remote.active_embedding(v)==v.embedding and reads==[(object_key,len(pdf))]
    assert worker.run_once()=='idle'
    assert all(r['reserved_input_tokens']==0 for r in db('SELECT * FROM server_dispatch_budget_limits WHERE window_key=%s',(reviews.contract['window_key'],)))
    assert reviews.view(remote.owner,key)['result_ready']
    reviews.cancel(remote.owner,key)
    assert row_for(db,a)['state']=='staged'


class Response:
    status_code=200
    def json(self):return {'status':'completed','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps({'text':'합성 근거를 한국어로 번역합니다.'})}]}],'usage':{'input_tokens':50,'output_tokens':20}}
    def close(self):pass


def provider(db,a,row,*,post):
    p=row['snapshot']['preview']
    chunk=db('''SELECT c.chunk_id,c.original_text FROM server_processing_chunks c LEFT JOIN server_translation_chunks t
        ON t.owner_user_id=c.owner_user_id AND t.chunk_id=c.chunk_id AND t.translation_revision_id=%s
        WHERE c.owner_user_id=%s AND c.parse_revision_id=%s AND t.chunk_id IS NULL''',
        (p['translation_revision_id'],a.owner,p['parse_revision_id']))[0]
    config=OpenAITranslationConfig('synthetic-key',TranslationSettings('openai','synthetic','ko-translation-v1'),max_output_tokens=100,allow_external_api=True)
    captured=[]
    capture=SimpleNamespace(send=lambda settings,payload,stage:captured.append(payload) or Response().json())
    OpenAITranslationProvider(config,transport=capture).translate(TranslationRequest(str(chunk['chunk_id']),p['parse_revision_id'],chunk['original_text'],recovery_policy='layout-aware-v1'),config.translation_settings)
    approved=dict(max_requests=1,max_input_tokens=100,max_output_tokens=100,payloads={payload_digest(captured[0]):100})
    return translation_factory(config,approved,post=post)


def test_real_translation_adapter_only_repairs_missing_and_never_replays(db,stores,pool):
    remote,v,reviews,a,row=ready(db,stores,pool)
    sent=[]
    def post(*args,**kwargs):sent.append(kwargs);return Response()
    factory=provider(db,a,row,post=post)
    db("UPDATE server_dispatch_controls SET dispatch_enabled=true WHERE control_id='provider_dispatch'")
    errors=[]
    def stage(row,worker):
        try:return ReprocessingStages(pool,None,factory)(row,worker)
        except Exception as error:errors.append(error);raise
    worker=ReprocessingWorker(reviews,a,stage,execution_enabled=True)
    status=worker.run_once()
    if errors:raise errors[0]
    assert status=='staged'
    result=row_for(db,a)
    assert len(sent)==1 and sent[0]['allow_redirects'] is False
    assert len(result['attempts'])==1 and result['charged'] and not result['budget_reserved']
    assert len(result['staged_result']['translations'])==8
    assert sum(t['text']=='합성 근거를 한국어로 번역합니다.' for t in result['staged_result']['translations'])==1
    assert remote.active_embedding(v)==v.embedding
    assert worker.run_once()=='idle' and len(sent)==1
    for budget in db('SELECT * FROM server_dispatch_budget_limits WHERE window_key=%s',(reviews.contract['window_key'],)):
        assert budget['reserved_input_tokens']==0 and budget['consumed_input_tokens']==50


@pytest.mark.parametrize('change',['cancel','revoke','delete','publication','expiry'])
def test_late_stage_result_is_rejected(db,stores,pool,change):
    remote,v,reviews,a,row=ready(db,stores,pool)
    def stages(row,worker):
        if change=='cancel':reviews.cancel(a.owner,a.request_key)
        elif change=='revoke':reviews.cancel(a.owner,a.request_key,'storage')
        elif change=='delete':db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(v.paper,))
        elif change=='publication':remote.publish(100,base=v)
        else:db("UPDATE server_reprocessing_reviews SET lease_until=clock_timestamp()-interval '1 second' WHERE request_key=%s",(a.request_key,))
        return {'forbidden':True}
    assert ReprocessingWorker(reviews,a,stages,execution_enabled=True).run_once()=='blocked'
    assert row_for(db,a)['staged_result'] is None
    assert not row_for(db,a)['budget_reserved']


def test_lease_generation_fences_old_worker_and_concurrent_claims(db,stores,pool):
    _,_,reviews,a,_=ready(db,stores,pool)
    workers=[ReprocessingWorker(reviews,a,lambda *_:{},execution_enabled=True) for _ in range(2)]
    with ThreadPoolExecutor(2) as executor:claims=list(executor.map(lambda w:w.claim(),workers))
    assert sum(c is not None for c in claims)==1
    old=workers[next(i for i,c in enumerate(claims) if c is not None)]
    db("UPDATE server_reprocessing_reviews SET lease_until=clock_timestamp()-interval '1 second' WHERE request_key=%s",(a.request_key,))
    new=ReprocessingWorker(reviews,a,lambda *_:{'new':True},execution_enabled=True)
    assert new.claim() is not None
    with pytest.raises(ValueError):old.checkpoint()
    new.checkpoint()


def test_unknown_provider_is_not_retried_or_refunded(db,stores,pool):
    _,_,reviews,a,_=ready(db,stores,pool)
    sent=[]
    def stages(row,worker):
        def transmit():sent.append(1);raise TimeoutError('secret')
        return worker.dispatch('a'*64,100,100,transmit)
    db("UPDATE server_dispatch_controls SET dispatch_enabled=true WHERE control_id='provider_dispatch'")
    worker=ReprocessingWorker(reviews,a,stages,execution_enabled=True)
    assert worker.run_once()=='blocked' and sent==[1]
    reviews.cancel(a.owner,a.request_key)
    assert worker.run_once()=='idle' and row_for(db,a)['failure_code']=='execution_failed'
    assert row_for(db,a)['charged']


def test_disabled_wrong_hash_and_dispatch_pause_never_send(db,stores,pool):
    _,_,reviews,a,_=ready(db,stores,pool)
    sent=[]
    stages=lambda row,w:w.dispatch('a'*64,100,100,lambda:sent.append(1))
    with pytest.raises(ValueError):ReprocessingWorker(reviews,a,stages).run_once()
    with pytest.raises(ValueError):ReprocessingWorker(reviews,replace(a,input_hash='b'*64),stages,execution_enabled=True).run_once()
    db("UPDATE server_dispatch_controls SET dispatch_enabled=false WHERE control_id='provider_dispatch'")
    assert ReprocessingWorker(reviews,a,stages,execution_enabled=True).run_once()=='blocked'
    assert not sent and not row_for(db,a)['charged']


def test_crash_after_dispatch_does_not_start_new_generation(db,stores,pool):
    _,_,reviews,a,_=ready(db,stores,pool)
    worker=ReprocessingWorker(reviews,a,lambda *_:{},execution_enabled=True)
    worker.claim()
    db("UPDATE server_dispatch_controls SET dispatch_enabled=true WHERE control_id='provider_dispatch'")
    def interrupt():raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):worker.dispatch('a'*64,100,100,interrupt)
    db("UPDATE server_reprocessing_reviews SET lease_until=clock_timestamp()-interval '1 second' WHERE request_key=%s",(a.request_key,))
    restarted=ReprocessingWorker(reviews,a,lambda *_:pytest.fail('재전송 금지'),execution_enabled=True)
    assert restarted.run_once()=='idle'
    assert row_for(db,a)['failure_code']=='provider_result_uncertain'


def test_cancel_during_transmission_keeps_charge_and_discards_result(db,stores,pool):
    from solo_leveling.application.provider_dispatch import ProviderResponse
    _,_,reviews,a,_=ready(db,stores,pool)
    db("UPDATE server_dispatch_controls SET dispatch_enabled=true WHERE control_id='provider_dispatch'")
    def stages(row,worker):
        def send():reviews.cancel(a.owner,a.request_key);return ProviderResponse({},10,10)
        return worker.dispatch('a'*64,100,100,send)
    worker=ReprocessingWorker(reviews,a,stages,execution_enabled=True)
    assert worker.run_once()=='blocked'
    row=row_for(db,a)
    assert row['state']=='cancelled' and row['charged'] and row['staged_result'] is None
    assert row['attempts'][0]['input_tokens']==10


def test_usage_overrun_is_recorded_and_no_result_is_staged(db,stores,pool):
    from solo_leveling.application.provider_dispatch import ProviderResponse
    _,_,reviews,a,_=ready(db,stores,pool)
    db("UPDATE server_dispatch_controls SET dispatch_enabled=true WHERE control_id='provider_dispatch'")
    stages=lambda row,w:w.dispatch('a'*64,100,100,lambda:ProviderResponse({},130,120))
    assert ReprocessingWorker(reviews,a,stages,execution_enabled=True).run_once()=='blocked'
    row=row_for(db,a)
    assert row['attempts'][0]['input_tokens']==130 and row['staged_result'] is None
    assert all(r['consumed_input_tokens']==4030 for r in db('SELECT * FROM server_dispatch_budget_limits WHERE window_key=%s',(reviews.contract['window_key'],)))
