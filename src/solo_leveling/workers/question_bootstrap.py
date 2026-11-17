"""승인한 고정 질문 생성 설정과 전용 DB를 연결한다. 사전 검사는 외부 호출이 없다."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlsplit, unquote, parse_qsl

from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.domain.remote_processing import EmbeddingSpec
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.generation.semantic_verifier import SemanticSettings
from solo_leveling.workers.question_composition import QuestionExecutionFactory
from solo_leveling.workers.remote_question import RemoteQuestionRunner, PersistedQuestionExecutor
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from .verification_bootstrap import read_json, ApprovedPayloadBounds, preflight, _pool


def require(ok):
    if not ok:
        raise ValueError('question_bootstrap_invalid')


@dataclass(frozen=True, repr=False)
class Plan:
    raw: dict
    policy: RemoteExecutionPolicy
    spec: EmbeddingSpec


def load_plan(path):
    raw = read_json(path)
    require(type(raw) is dict and set(raw) == {
        'schema_version','database_name','schema_name','database_role','policy','generation','provider_profile_id','window_key',
        'runtime_image','runtime_lock_path','model_lock_path','model_dir','embedding_spec','requests'})
    require(type(raw['schema_version']) is int and raw['schema_version']==1)
    for key in ('database_name','schema_name','database_role'):
        require(type(raw[key]) is str and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}',raw[key]))
    for key in ('provider_profile_id','window_key'):
        require(type(raw[key]) is str and bool(raw[key].strip()))
    for key in ('runtime_lock_path','model_lock_path','model_dir'):
        require(type(raw[key]) is str and Path(raw[key]).is_absolute())
    require(type(raw['runtime_image']) is str and re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}',raw['runtime_image']))
    policy = RemoteExecutionPolicy(**raw['policy'])
    generation = raw['generation']
    require(type(generation) is dict and set(generation)=={'model','timeout_seconds','max_output_tokens'})
    settings = OpenAIGenerationSettings('configuration-only',**generation)
    require(settings.timeout_seconds<=policy.stage_timeout_seconds and settings.max_output_tokens<=policy.max_output_tokens)
    bounds = ApprovedPayloadBounds(settings.model,settings.max_output_tokens,raw['requests'])
    require(bounds.max_requests<=policy.max_calls and bounds.max_input<=policy.max_input_tokens
            and bounds.max_output<=policy.max_output_tokens)
    from solo_leveling.application.evidence_qa.remote_search import canonical_fingerprint
    canonical_fingerprint(json.dumps(raw['embedding_spec']))
    spec = EmbeddingSpec(**raw['embedding_spec'])
    require(spec.runtime=='pytorch' and spec.precision=='float32' and spec.pooling=='mean'
            and re.fullmatch('[0-9a-f]{64}',spec.artifact_sha256))
    require(spec.normalization=='l2' and spec.model_id==spec.tokenizer_id
            and spec.model_revision==spec.tokenizer_revision==Path(raw['model_dir']).name)
    return Plan(raw,policy,spec)


def check_files(plan):
    preflight(SimpleNamespace(raw=plan.raw,spec=plan.spec))


def _embedding(plan):
    from solo_leveling.infrastructure.embeddings.processing_embedder import PyTorchProcessingEmbedder
    from solo_leveling.infrastructure.embeddings.remote_query import PinnedQueryEmbeddingRuntime
    return PinnedQueryEmbeddingRuntime(PyTorchProcessingEmbedder(model_dir=Path(plan.raw['model_dir']),
        batch_size=1,check_allowed=lambda: True),plan.spec)


@contextmanager
def build(plan, source, *, pool_factory=_pool, check=check_files, embedding_factory=_embedding, post=None):
    # 일반 OPENAI_API_KEY나 웹 DATABASE_URL로 조용히 대체하지 않는다.
    require(source.get('QUESTION_WORKER_EXECUTION_ENABLED')=='true')
    check(plan)
    dsn, key = source.get('QUESTION_WORKER_DATABASE_URL'),source.get('QUESTION_WORKER_OPENAI_API_KEY')
    require(type(dsn) is str and type(key) is str and bool(key.strip()))
    url = urlsplit(dsn)
    require(url.scheme in ('postgres','postgresql') and url.hostname and url.username and url.password
            and unquote(url.username)==plan.raw['database_role']
            and unquote(url.path.lstrip('/'))==plan.raw['database_name'] and not url.fragment)
    query = parse_qsl(url.query,keep_blank_values=True)
    require(len(query)<=1 and all(k=='sslmode' for k,v in query))
    require(dict(query).get('sslmode')=='verify-full' or url.hostname in ('localhost','127.0.0.1','::1')
            and dict(query).get('sslmode') in (None,'disable'))
    pool = pool_factory(dsn,plan.raw['schema_name'])
    try:
        pool.open(wait=True,timeout=10)
        with pool.connection() as conn:
            identity = conn.execute('SELECT current_database() AS database, current_schema() AS schema').fetchone()
            require(identity=={'database':plan.raw['database_name'],'schema':plan.raw['schema_name']})
            role=conn.execute('SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user').fetchone()
            require(role and role['rolname']==plan.raw['database_role'] and not any(role[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls','rolinherit')))
        generation=OpenAIGenerationSettings(key,**plan.raw['generation'],allow_external_api=True)
        factory=QuestionExecutionFactory(pool,generation=generation,semantic=SemanticSettings(),
            provider_profile_id=plan.raw['provider_profile_id'],window_key=plan.raw['window_key'],
            embedding_runtime=embedding_factory(plan),token_upper_bound=ApprovedPayloadBounds(
                generation.model,generation.max_output_tokens,plan.raw['requests']),post=post)
        def execute(intent,policy,allowed,request):
            require(policy==plan.policy)
            return factory(intent,policy,allowed,request)
        store=PostgresRemoteExecutionStore(pool,question_policy_version=plan.policy.version)
        yield RemoteQuestionRunner(store,PersistedQuestionExecutor(store,execute))
    finally:
        pool.close()


@contextmanager
def factory():
    try:
        plan=load_plan(os.environ.get('QUESTION_WORKER_CONFIG',''))
        with build(plan,os.environ) as runner:
            yield runner
    except Exception:
        raise RuntimeError('question_bootstrap_failed') from None


def main(argv=None):
    from argparse import ArgumentParser
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--check-files',action='store_true')
    args=parser.parse_args(argv)
    try:
        plan=load_plan(args.config)
        if args.check_files: check_files(plan)
    except Exception:
        print(json.dumps({'status':'question_bootstrap_invalid'}))
        return 1
    print(json.dumps({'status':'question_files_verified' if args.check_files else 'question_config_valid'}))
    return 0


def preview_payload(pool, plan, owner, execution_id, embedding_runtime):
    """내부 승인 준비용 최초 전송 본문. 반환 본문은 민감하므로 로그·티켓에 기록하지 않는다."""
    from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
    from solo_leveling.application.evidence_qa.remote_answer import RemoteAnswerPreparationService
    from solo_leveling.application.evidence_qa.remote_search import RemoteContextSearchService, FingerprintCheckedEmbedder
    from solo_leveling.interfaces.http.remote_vectors import PostgresRemoteScopedSearch
    from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
    from solo_leveling.interfaces.http.remote_sources import PostgresRemoteSourceReader
    from solo_leveling.infrastructure.generation.openai_generator import OpenAIClaimGenerator
    from .question_composition import execution_ids
    with pool.connection() as conn:
        row=conn.execute('''SELECT question_snapshot,policy_snapshot,input_hash FROM server_remote_executions
            WHERE owner_user_id=%s AND execution_id=%s AND state='queued' ''',(owner,execution_id)).fetchone()
    require(row and row['policy_snapshot']==plan.policy.snapshot())
    request=RemoteQuestionInput(json.dumps(row['question_snapshot']))
    binding,question,filters,top_k,config=request.unpack()
    require(binding.owner_user_id==owner and request.input_hash==row['input_hash'])
    settings=OpenAIGenerationSettings('preview-only',**plan.raw['generation'],allow_external_api=True)
    expected=QuestionExecutionFactory(pool,generation=settings,semantic=SemanticSettings(),
        provider_profile_id=plan.raw['provider_profile_id'],window_key=plan.raw['window_key'],
        embedding_runtime=embedding_runtime,token_upper_bound=lambda *_:0).processing_config(plan.policy.version)
    require(config==expected)
    resolver=PostgresRemoteContextResolver(pool)
    require(resolver.resolve(owner,binding.context_id)==binding)
    class Captured(BaseException):
        def __init__(self,payload):self.payload=payload
    class CaptureTransport:
        def send(self,settings,payload,stage):
            require(stage=='generation')
            raise Captured(payload)
    service=RemoteAnswerPreparationService(RemoteContextSearchService(lambda context: resolver.resolve(owner,context),
        PostgresRemoteScopedSearch(pool,FingerprintCheckedEmbedder(embedding_runtime))),
        PostgresRemoteSourceReader(pool),OpenAIClaimGenerator(settings,transport=CaptureTransport()),
        id_factory=execution_ids(execution_id))
    try:
        service.prepare(question,binding.context_id,filters=filters,top_k=top_k)
    except Captured as captured:
        return captured.payload
    raise ValueError('question_preview_unavailable')


if __name__=='__main__':
    raise SystemExit(main())
