"""합성 결과·로컬 벡터로 후보 게시와 명시적 색인 선택의 원자성을 검증한다."""
# ruff: noqa: F811
from dataclasses import asdict,replace
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db  # noqa: F401
from tests.integration.test_processing_staging_postgres import setup as initial
from tests.integration.test_remote_reprocessing_postgres import CONFIG,grant
from tests.fixtures.processing_result import SPEC
from solo_leveling.domain.remote_processing import EmbeddingBatch,EmbeddingFragment,ProcessedTranslation
from solo_leveling.domain.translation import TranslationStatus,TranslationFailureCode
from solo_leveling.domain.embedding_windows import TokenWindowPolicy
from solo_leveling.workers.processing_publication import PostgresStagedProcessingPublisher
from solo_leveling.workers.remote_reprocessing import ReprocessingWorker,ReprocessingApproval
from solo_leveling.workers.reprocessing_publication import ReprocessingPublisher
from solo_leveling.interfaces.http.remote_reprocessing import PostgresReprocessingReviews
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='로컬 PostgreSQL 필요')


def setup(pool,db,operation='translation_recovery'):
    store,value=initial(pool,db)
    c=value.document.chunks[0]
    value=replace(value,translations=(ProcessedTranslation(c.chunk_id,c.original_text,TranslationFailureCode.EMPTY_TRANSLATION,TranslationStatus.ORIGINAL_ONLY),))
    saved=store.save(value)
    old=PostgresStagedProcessingPublisher(store).publish(value.source,saved.payload_sha256,expected_active_publication_id=None)
    owner=value.source.owner_user_id;context=str(uuid4());key=str(uuid4())
    PostgresRemoteContextResolver(pool).pin(owner,context,old.paper_id,old.server_version_id)
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성') ON CONFLICT DO NOTHING",(owner,))
    config=dict(CONFIG,window_key=str(uuid4()))
    for scope,id_ in [('owner',owner),('service','service')]:
        db('INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens) VALUES (%s,%s,%s,%s,8000,2000)',
           (scope,id_,config['policy_version'],config['window_key']))
    db("UPDATE server_dispatch_controls SET intake_enabled=true WHERE control_id='provider_dispatch'")
    reviews=PostgresReprocessingReviews(pool,config=config)
    reviews.prepare(owner,key,context,operation);grant(reviews,owner,key);reviews.submit(owner,key)
    row=db('SELECT * FROM server_reprocessing_reviews WHERE request_key=%s',(key,))[0]
    approval=ReprocessingApproval(owner,key,row['input_hash'])
    if operation=='translation_recovery':
        staged=dict(schema_version=1,operation=operation,publication_ready=False,source_publication_id=old.publication_id,
            parse_revision_id=old.parse_revision_id,translation_revision_id=str(uuid4()),
            translations=[dict(chunk_id=c.chunk_id,text='새 번역 결과입니다.',status='translated')])
    else:
        parse=str(uuid4());new_chunk=replace(c,chunk_id=str(uuid4()),parse_revision_id=parse)
        sections=[asdict(replace(s,parse_revision_id=parse)) for s in value.document.sections]
        staged=dict(schema_version=1,operation=operation,publication_ready=False,source_publication_id=old.publication_id,
            parse_revision_id=parse,chunks=[asdict(new_chunk)],sections=sections,page_count=2)
    assert ReprocessingWorker(reviews,approval,lambda *_:staged,execution_enabled=True).run_once()=='staged'
    return reviews,approval,old,context


def embedder(embedding,allowed):
    def embed(items,spec):
        assert allowed()
        fragments=tuple(EmbeddingFragment(sha256(f'{embedding}:{i.chunk_id}:{start}'.encode()).hexdigest(),embedding,i.chunk_id,start,start+2,3)
                        for i in items for start in (0,1))
        return EmbeddingBatch(spec,tuple(i.chunk_id for i in items),tuple((1.,0.) for f in fragments),fragments,
                              TokenWindowPolicy(2,1).fingerprint())
    return SimpleNamespace(embed=embed)


def publisher(reviews,a,factory=embedder):return ReprocessingPublisher(reviews,a,SPEC,factory,execution_enabled=True)


@pytest.mark.parametrize('operation',['translation_recovery','reparse'])
def test_candidate_atomic_publication_search_and_explicit_selection(pool,db,operation):
    reviews,a,old,context=setup(pool,db,operation)
    pub=publisher(reviews,a).run_once()
    assert pub!=old.publication_id and publisher(reviews,a).run_once()==pub
    assert str(db('SELECT active_publication_id FROM server_processing_versions WHERE paper_id=%s',(old.paper_id,))[0]['active_publication_id'])==old.publication_id
    view=reviews.view(a.owner,a.request_key)
    assert view['publication_ready'] and view['publication']['selected_context_id'] is None
    candidate=view['publication'];embedding=candidate['embedding_set_id']
    assert db('SELECT count(*) n FROM server_embedding_fragments WHERE embedding_set_id=%s',(embedding,))[0]['n']==2
    assert db('SELECT count(*) n FROM server_embedding_vectors WHERE embedding_set_id=%s',(embedding,))[0]['n']==0
    if operation=='translation_recovery':assert candidate['parse_revision_id']==old.parse_revision_id
    else:
        assert candidate['parse_revision_id']!=old.parse_revision_id
        assert db('SELECT translation_status FROM server_translation_chunks WHERE translation_revision_id=%s',(candidate['translation_revision_id'],))[0]['translation_status']=='original_only'
    with ThreadPoolExecutor(2) as executor:
        selected=list(executor.map(lambda _:reviews.select(a.owner,a.request_key,pub),range(2)))
    contexts={s['publication']['selected_context_id'] for s in selected}
    assert len(contexts)==1 and context not in contexts
    resolver=PostgresRemoteContextResolver(pool)
    assert resolver.resolve(a.owner,context).publication_id==old.publication_id
    selected_context=next(iter(contexts));binding=resolver.resolve(a.owner,selected_context)
    assert binding.publication_id==pub and binding.embedding_set_id==embedding
    assert resolver.resolve('other',selected_context) is None
    assert reviews.select('other',a.request_key,pub) is None
    # 실제 pgvector 조각 검색을 새 context로 제한한다.
    from solo_leveling.interfaces.http.remote_vectors import PostgresRemoteScopedSearch
    from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
    search=PostgresRemoteScopedSearch(pool,lambda *_:(1.,0.))
    hits=search.search('합성',binding,RemoteSearchFilters(),10)
    assert len(hits)==1 and hits[0].binding.publication_id==pub
    assert db('SELECT count(*) n FROM server_processing_publications WHERE owner_user_id=%s',(a.owner,))[0]['n']==2


@pytest.mark.parametrize('change',['cancel','revoke','delete','pointer','changed_stage','invalid_vectors','wrong_mapping'])
def test_changed_authority_or_invalid_embedding_leaves_no_partial_rows(pool,db,change):
    reviews,a,old,_=setup(pool,db)
    def factory(embedding,allowed):
        def embed(items,spec):
            batch=embedder(embedding,allowed).embed(items,spec)
            if change=='cancel':reviews.cancel(a.owner,a.request_key)
            elif change=='revoke':reviews.cancel(a.owner,a.request_key,'storage')
            elif change=='delete':db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(old.paper_id,))
            elif change=='pointer':db('UPDATE server_processing_versions SET active_publication_id=NULL WHERE paper_id=%s',(old.paper_id,))
            elif change=='changed_stage':db("UPDATE server_reprocessing_reviews SET staged_result=staged_result||'{\"extra\":1}'::jsonb WHERE request_key=%s",(a.request_key,))
            elif change=='invalid_vectors':batch=replace(batch,vectors=((0.,0.),)*2)
            else:batch=replace(batch,fragments=batch.fragments[:1],vectors=batch.vectors[:1])
            return batch
        return SimpleNamespace(embed=embed)
    with pytest.raises(Exception):publisher(reviews,a,factory).run_once()
    assert db('SELECT count(*) n FROM server_processing_publications WHERE owner_user_id=%s',(a.owner,))[0]['n']==1
    assert db('SELECT count(*) n FROM server_translation_revisions WHERE owner_user_id=%s',(a.owner,))[0]['n']==1


def test_injected_materialization_failure_rolls_back_and_can_retry(pool,db,monkeypatch):
    reviews,a,old,_=setup(pool,db)
    from solo_leveling.interfaces.http.remote_fragment_vectors import PostgresEmbeddingFragmentWriter
    original=PostgresEmbeddingFragmentWriter.put
    def fail(self,*args):original(self,*args);raise RuntimeError('합성 저장 오류')
    with monkeypatch.context() as m:
        m.setattr(PostgresEmbeddingFragmentWriter,'put',fail)
        with pytest.raises(RuntimeError):publisher(reviews,a).run_once()
    assert db('SELECT count(*) n FROM server_processing_jobs WHERE job_id=(SELECT job_id FROM server_reprocessing_reviews WHERE request_key=%s)',(a.request_key,))[0]['n']==0
    assert publisher(reviews,a).run_once()!=old.publication_id


def test_wrong_model_and_selection_conflict_are_rejected(pool,db):
    reviews,a,old,_=setup(pool,db)
    with pytest.raises(ValueError):ReprocessingPublisher(reviews,a,replace(SPEC,model_id='other'),lambda *_:pytest.fail('모델 로딩 금지'),execution_enabled=True).run_once()
    pub=publisher(reviews,a).run_once()
    with pytest.raises(ValueError):reviews.select(a.owner,a.request_key,str(uuid4()))
    db('UPDATE server_processing_versions SET active_publication_id=NULL WHERE paper_id=%s',(old.paper_id,))
    with pytest.raises(ValueError):reviews.select(a.owner,a.request_key,pub)


def test_concurrent_publication_replays_one_result_and_revoked_selection_is_blocked(pool,db):
    reviews,a,_,_=setup(pool,db)
    with ThreadPoolExecutor(2) as executor:
        pubs=list(executor.map(lambda _:publisher(reviews,a).run_once(),range(2)))
    assert len(set(pubs))==1
    reviews.cancel(a.owner,a.request_key,'storage')
    with pytest.raises(ValueError):reviews.select(a.owner,a.request_key,pubs[0])
    assert db('SELECT count(*) n FROM server_processing_publications WHERE owner_user_id=%s',(a.owner,))[0]['n']==2
