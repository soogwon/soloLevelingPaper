"""고정 질문 입력을 원격 검색·생성·원자 저장에 연결하는 서버 전용 조립체."""
from copy import deepcopy
import hashlib
import json

from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.infrastructure.generation.openai_generator import OpenAIClaimGenerator, OpenAIGenerationSettings
from solo_leveling.infrastructure.generation.semantic_verifier import OpenAISemanticVerifier, SemanticSettings
from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
from solo_leveling.infrastructure.retrieval.remote_answer_factory import create_persisted_remote_answer_service
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from .remote_question import RemoteQuestionRunner, PersistedQuestionExecutor


class QuestionExecutionFactory:
    """서버 bootstrap이 명시한 제공자 설정만 사용한다. 운영 기본값이나 자동 재시도는 없다."""
    def __init__(self, pool, *, generation, semantic, provider_profile_id, embedding_runtime,
                 window_key, token_upper_bound, post=None):
        if (not isinstance(generation,OpenAIGenerationSettings) or not isinstance(semantic,SemanticSettings)
                or not isinstance(provider_profile_id,str) or not provider_profile_id.strip()
                or not isinstance(window_key,str) or not window_key.strip()
                or not callable(token_upper_bound)):
            raise ValueError('remote_question_config_invalid')
        self.pool,self.generation,self.semantic=pool,generation,semantic
        self.profile,self.embedding=provider_profile_id,embedding_runtime
        self.window,self.estimate,self.post=window_key,token_upper_bound,post

    def processing_config(self, policy_version):
        return dict(generation_model=self.generation.model,
            generation_prompt_version=OpenAIClaimGenerator.prompt_version,
            semantic_model=(self.semantic.model or self.generation.model) if self.semantic.enabled else 'disabled',
            semantic_prompt_version=OpenAISemanticVerifier.prompt_version if self.semantic.enabled else 'disabled',
            budget_policy_version=policy_version,provider_profile_id=self.profile)

    def __call__(self, intent, policy, allowed, request):
        if not self.generation.allow_external_api or self.generation.local_only:
            raise DispatchError('DISPATCH_DISABLED')
        binding,question,filters,top_k,config=request.unpack()
        if (config!=self.processing_config(policy.version) or binding.owner_user_id!=intent.owner_user_id
                or request.input_hash!=intent.input_hash or not allowed()):
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        if (self.generation.max_output_tokens>policy.max_output_tokens
                or self.semantic.enabled and self.semantic.max_output_tokens>policy.max_output_tokens):
            raise DispatchError('DISPATCH_NOT_AUTHORIZED')
        counters={}
        def prepare(stage,payload,digest):
            if stage not in ('generation','repair','coverage','semantic_verification') or not allowed():
                raise DispatchError('DISPATCH_NOT_AUTHORIZED')
            upper=self.estimate(payload['model'],deepcopy(payload))
            if type(upper) is not int or upper<1:
                raise DispatchError('DISPATCH_NOT_AUTHORIZED')
            index=counters.get(stage,0)
            counters[stage]=index+1
            # 재시작으로 같은 논리 전송 슬롯이 다른 key가 되지 않는다.
            key=hashlib.sha256(json.dumps(['remote-question-v1',intent.execution_id,stage,index],separators=(',',':')).encode()).hexdigest()
            return PreparedProviderAttempt(key,digest,upper)
        transport=create_persisted_responses_transport(self.pool,intent,policy.version,self.window,prepare,post=self.post)
        generator=OpenAIClaimGenerator(self.generation,transport=transport)
        verification=None
        if self.semantic.enabled:
            verification=SemanticVerificationService(OpenAISemanticVerifier(self.generation,self.semantic,transport=transport),
                max_claims=self.semantic.max_claims,max_input_chars=self.semantic.max_input_chars)
        resolver=PostgresRemoteContextResolver(self.pool)
        def resolve(context):
            if not allowed():
                raise DispatchError('DISPATCH_NOT_AUTHORIZED')
            current=resolver.resolve(intent.owner_user_id,context)
            if current!=binding:
                raise DispatchError('DISPATCH_NOT_AUTHORIZED')
            return current
        service=create_persisted_remote_answer_service(self.pool,resolve,self.embedding,generator,config,
            semantic_verification=verification,check_allowed=allowed,id_factory=execution_ids(intent.execution_id))
        return service.execute(intent,question,binding.context_id,filters=filters,top_k=top_k)


def create_question_runner(pool, **options):
    store=PostgresRemoteExecutionStore(pool)
    factory=QuestionExecutionFactory(pool,**options)
    return RemoteQuestionRunner(store,PersistedQuestionExecutor(store,factory))


def execution_ids(execution_id):
    """동일 실행의 오프라인 본문과 실제 전송에서 근거 ID를 동일하게 만든다."""
    from itertools import count
    from uuid import UUID, uuid5
    namespace = UUID(str(execution_id))
    sequence = count()
    return lambda: str(uuid5(namespace, f'question-evidence-v1:{next(sequence)}'))
