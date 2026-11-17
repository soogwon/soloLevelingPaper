"""서버 설정만으로 작업별 파서·번역 gate·고정 모델을 조립한다."""

import hashlib
import json

from solo_leveling.application.provider_dispatch import ProviderDispatch
from solo_leveling.application.remote_processing import RemoteProcessingStages
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.remote_processing import ProcessingError
from solo_leveling.infrastructure.embeddings.processing_embedder import PyTorchProcessingEmbedder
from solo_leveling.infrastructure.generation.gated_responses import GatedResponsesTransport, PreparedProviderAttempt
from solo_leveling.infrastructure.parsing.processing_parser import ProcessingPdfParser
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, OpenAITranslationProvider
from solo_leveling.infrastructure.translation.processing_translation import GatedProcessingTranslation


class ProcessingStagesFactory:
    """공개 요청 입력이 아닌 신뢰하는 서버 bootstrap에서만 생성한다.

    상한 추정기·예산 정책·제한값·모델 지문은 서버가 명시적으로 공급한다.
    post/model_factory는 검증용 대역 주입이며 생략하면 실제 구현을 사용한다.
    """

    def __init__(self, authorization, *, service_config, model_dir, embedding_spec,
                 limits, retry_policy, policy_version, window_key, token_upper_bound,
                 batch_size, post=None, model_factory=None):
        if (not isinstance(service_config, OpenAITranslationConfig)
                or not callable(token_upper_bound)
                or not service_config.allow_external_api or service_config.local_only):
            raise ProcessingError('PROCESSING_CONFIG_INVALID')
        self.authorization = authorization
        self.config, self.model_dir, self.spec = service_config, model_dir, embedding_spec
        self.limits, self.retry_policy = limits, retry_policy
        self.policy_version, self.window_key = policy_version, window_key
        self.token_upper_bound, self.batch_size = token_upper_bound, batch_size
        self.post, self.model_factory = post, model_factory

    def __call__(self, source, check_allowed):
        if check_allowed(source, 'parse') is not True:
            raise ProcessingError('PROCESSING_NOT_ALLOWED')
        auth, config, estimate = self.authorization, self.config, self.token_upper_bound
        intent = auth.dispatch_intent(source)
        if (intent.owner_user_id, intent.execution_id, intent.lease_token) != (
                source.owner_user_id, source.job_id, source.lease_token):
            raise ProcessingError('PROCESSING_TRANSLATION_CONFIG_MISMATCH')
        ledger = auth.ledger(source, check_allowed, policy_version=self.policy_version,
                             window_key=self.window_key)
        dispatch = ProviderDispatch(ledger, intent)
        chunks = {}

        class Parser:
            def parse(self, pdf, revision):
                result = ProcessingPdfParser().parse(pdf, revision)
                chunks.clear()
                chunks.update((c.chunk_id, c) for c in result.chunks)
                return result

        def prepare(current, current_intent, request, attempt, stage, payload, digest):
            chunk = chunks.get(request.chunk_id)
            if (current != source or current_intent != intent or stage != 'translation'
                    or chunk is None or request.parse_revision_id != source.parse_revision_id
                    or request.original_text != chunk.original_text):
                raise ProcessingError('PROCESSING_TRANSLATION_CONFIG_MISMATCH')
            upper = estimate(config.translation_settings.model, payload)
            if type(upper) is not int or upper < 1:
                raise ProcessingError('PROCESSING_CONFIG_INVALID')
            # 새 lease/리비전이나 다른 payload로 같은 논리 전송 슬롯을 재사용하지 않는다.
            # 저장 번역 재사용 전까지 재claim은 기존 슬롯에서 안전하게 거부된다.
            raw = json.dumps(['remote-translation-v1', source.job_id, chunk.chunk_index, attempt],
                             separators=(',', ':')).encode('utf-8')
            key = hashlib.sha256(raw).hexdigest()
            return PreparedProviderAttempt(key, digest, upper)

        def unscoped(*args):
            raise ProcessingError('PROCESSING_TRANSLATION_CONFIG_MISMATCH')

        transport = GatedResponsesTransport(dispatch, unscoped, post=self.post)
        provider = OpenAITranslationProvider(config, transport=transport)
        translator = GatedProcessingTranslation(
            TranslationService(provider, retry_policy=self.retry_policy), prepare_attempt=prepare,
        )
        options = {} if self.model_factory is None else {'model_factory': self.model_factory}
        embedder = PyTorchProcessingEmbedder(model_dir=self.model_dir,
            check_allowed=lambda: check_allowed(source, 'embedding'), batch_size=self.batch_size, **options)
        return RemoteProcessingStages(Parser(), translator, embedder, limits=self.limits,
            translation_settings=config.translation_settings, embedding_spec=self.spec,
            check_allowed=check_allowed)


def create_processing_worker(pool, objects, *, worker_id, lease_seconds,
                             max_staging_bytes, heartbeat_interval_seconds=None, **stage_options):
    """서버 소유 DB/R2 연결과 명시적 정책으로 실행 가능한 worker를 만든다."""
    from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
    from solo_leveling.workers.processing_authorization import PostgresProcessingAuthorization
    from solo_leveling.workers.remote_processing import RemoteProcessingWorker

    from solo_leveling.workers.processing_staging import PostgresProcessingResultStaging

    staging = PostgresProcessingResultStaging(pool, limits=stage_options["limits"],
        translation_settings=stage_options["service_config"].translation_settings,
        embedding_spec=stage_options["embedding_spec"], max_result_bytes=max_staging_bytes)
    authorization = PostgresProcessingAuthorization(pool)
    factory = ProcessingStagesFactory(authorization, **stage_options)
    return RemoteProcessingWorker(PostgresProcessingJobStore(pool), authorization, objects, None,
        worker_id=worker_id, lease_seconds=lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds, stages_factory=factory, result_store=staging)
