"""복구 worker 단일 실행 설정. 승인 해시·제한 DB 역할·명시적 실행 flag가 필수다."""
from contextlib import contextmanager
import os
import re
from urllib.parse import urlsplit,unquote,parse_qsl

from solo_leveling.application.quiz.remote_contract import load
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
from solo_leveling.interfaces.http.object_storage import S3ObjectStorage
from .remote_reprocessing import ReprocessingApproval,create_worker,require
from .reprocessing_composition import translation_factory
from .quiz_runtime import _pool


def load_plan(path):
    from pathlib import Path
    with Path(path).open('rb') as stream: raw=stream.read(1048577)
    require(len(raw)<=1048576)
    plan=load(raw.decode('utf-8'))
    require(type(plan) is dict and set(plan) in ({'database_name','database_role','schema_name','approval','config','r2','translation'},
        {'database_name','database_role','schema_name','approval','config','r2','translation','publication'}))
    for name in ('database_name','database_role','schema_name'):
        require(type(plan[name]) is str and re.fullmatch('[a-z_][a-z0-9_]{0,62}',plan[name]))
    ReprocessingApproval(**plan['approval'])
    from solo_leveling.interfaces.http.remote_reprocessing import validate_contract
    validate_contract(plan['config'])
    return plan


def objects_for(plan, source):
    r2=plan['r2']
    if r2 is None:return None
    require(type(r2) is dict and set(r2)=={'endpoint','bucket','region'})
    url=urlsplit(r2['endpoint'])
    require(url.scheme=='https' and url.hostname and re.fullmatch('[a-f0-9]{32}\\.r2\\.cloudflarestorage\\.com',url.hostname)
            and not url.username and not url.password and not url.port and url.path in ('','/') and not url.query and not url.fragment)
    require(all(type(r2[k]) is str and r2[k].strip() for k in ('bucket','region')))
    key,secret=source.get('REPROCESSING_R2_ACCESS_KEY_ID'),source.get('REPROCESSING_R2_SECRET_ACCESS_KEY')
    require(type(key) is str and key.strip() and type(secret) is str and secret.strip())
    from .verification_bootstrap import _client
    return S3ObjectStorage(_client(r2,key,secret),r2['bucket'])


@contextmanager
def build(plan,source,*,pool_factory=_pool,post=None,publish=False):
    require(source.get('REPROCESSING_WORKER_EXECUTION_ENABLED')=='true')
    dsn=source.get('REPROCESSING_WORKER_DATABASE_URL')
    require(type(dsn) is str)
    url=urlsplit(dsn);query=parse_qsl(url.query,keep_blank_values=True)
    require(url.scheme in ('postgres','postgresql') and url.hostname and url.password and not url.fragment
            and unquote(url.username or '')==plan['database_role'] and unquote(url.path.lstrip('/'))==plan['database_name'])
    require(len(query)<=1 and all(k=='sslmode' for k,v in query))
    require(dict(query).get('sslmode')=='verify-full' or url.hostname in ('localhost','127.0.0.1','::1')
            and dict(query).get('sslmode') in (None,'disable'))
    pool=pool_factory(dsn,plan['schema_name'])
    try:
        pool.open(wait=True,timeout=10)
        with pool.connection() as conn:
            require(conn.execute('SELECT current_database() AS database,current_schema() AS schema').fetchone()
                    =={'database':plan['database_name'],'schema':plan['schema_name']})
            role=conn.execute('SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user').fetchone()
            require(role and role['rolname']==plan['database_role'] and not any(role[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls','rolinherit')))
        if publish:
            from pathlib import Path
            from solo_leveling.domain.remote_processing import EmbeddingSpec
            from solo_leveling.domain.embedding_windows import TokenWindowPolicy
            from solo_leveling.infrastructure.embeddings.processing_embedder import PyTorchProcessingEmbedder
            from solo_leveling.interfaces.http.remote_reprocessing import PostgresReprocessingReviews
            from .reprocessing_publication import ReprocessingPublisher
            config=plan.get('publication')
            require(type(config) is dict and set(config)=={'model_dir','embedding_spec','fragment_policy','batch_size'})
            require(Path(config['model_dir']).is_absolute())
            spec=EmbeddingSpec(**config['embedding_spec']);policy=TokenWindowPolicy(**config['fragment_policy'])
            def embedder(embedding,allowed):
                return PyTorchProcessingEmbedder(model_dir=Path(config['model_dir']),check_allowed=allowed,batch_size=config['batch_size'],
                    fragment_policy=policy,embedding_set_id=embedding)
            yield ReprocessingPublisher(PostgresReprocessingReviews(pool,config=plan['config']),
                ReprocessingApproval(**plan['approval']),spec,embedder,execution_enabled=True)
            return
        translator=None
        if plan['translation'] is not None:
            t=plan['translation']
            require(type(t) is dict and set(t)=={'timeout_seconds','max_output_tokens','requests'})
            config=OpenAITranslationConfig(source.get('REPROCESSING_OPENAI_API_KEY',''),
                TranslationSettings('openai',plan['config']['model'],'ko-translation-v1'),
                timeout_seconds=t['timeout_seconds'],max_output_tokens=t['max_output_tokens'],allow_external_api=True)
            translator=translation_factory(config,t['requests'],post=post)
        yield create_worker(pool,config=plan['config'],approval=ReprocessingApproval(**plan['approval']),
                            objects=objects_for(plan,source),translator_factory=translator,execution_enabled=True)
    finally:
        pool.close()


@contextmanager
def factory(*,publish=False):
    try:
        require(os.environ.get('REPROCESSING_WORKER_EXECUTION_ENABLED')=='true')
        plan=load_plan(os.environ.get('REPROCESSING_WORKER_CONFIG',''))
        with build(plan,os.environ,publish=publish) as worker:yield worker
    except Exception:
        raise RuntimeError('reprocessing_runtime_failed') from None
