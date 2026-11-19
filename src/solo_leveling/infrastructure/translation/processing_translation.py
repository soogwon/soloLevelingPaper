"""로컬 번역 서비스의 재시도/품질 검사를 유지하되 원격 전송 gate를 필수로 한다."""

from solo_leveling.application.provider_dispatch import ProviderDispatch
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import Chunk
from solo_leveling.domain.remote_processing import ProcessingError
from solo_leveling.infrastructure.generation.gated_responses import GatedResponsesTransport
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationProvider


class GatedProcessingTranslation:
    def __init__(self, service: TranslationService, *, prepare_attempt):
        if not isinstance(service, TranslationService) or not callable(prepare_attempt):
            raise ProcessingError('PROCESSING_CONFIG_INVALID')
        self.service = service
        # source + 청크/리비전 + 시도 순번을 영속 key/상한에 결합하는 신뢰 포트.
        # payload 본문만으로 서로 다른 청크의 전송 키를 추측하지 않는다.
        self.prepare_attempt = prepare_attempt
        self._provider()

    def _provider(self):
        provider = self.service.provider
        if (not isinstance(provider, OpenAITranslationProvider)
                or not isinstance(provider.transport, GatedResponsesTransport)
                or not isinstance(provider.transport.dispatch, ProviderDispatch)):
            raise ProcessingError('PROCESSING_TRANSLATION_GATE_REQUIRED')
        return provider

    def translate(self, source, chunks, settings, check):
        provider = self._provider()
        if (provider.transport.dispatch.intent.owner_user_id != source.owner_user_id
                or provider.config.translation_settings != settings
                or not provider.config.allow_external_api or provider.config.local_only):
            raise ProcessingError('PROCESSING_TRANSLATION_CONFIG_MISMATCH')
        # 외부가 보유한 provider의 transport 필드를 단계 실행 도중 바꿔도
        # 로컬 direct-HTTP 분기로 전환하지 않도록 이 호출의 adapter를 고정한다.
        config, transport, prepare_attempt = provider.config, provider.transport, self.prepare_attempt
        dispatch, post = transport.dispatch, transport.post
        intent, ledger, send = dispatch.intent, dispatch.ledger, dispatch.send
        attempts = {}

        class BoundDispatch:
            def send(self, *args, **kwargs):
                # 원장과 intent가 바뀐 객체로 같은 실행의 예산·동의를 대체하지 않는다.
                if dispatch.intent != intent or dispatch.ledger is not ledger:
                    raise ProcessingError('PROCESSING_TRANSLATION_CONFIG_MISMATCH')
                return send(*args, **kwargs)

        bound_dispatch = BoundDispatch()

        class CheckedProvider:
            def translate(self, request, current_settings):
                check()  # 각 청크·실제 재시도마다 확인. 전송의 원자 허용은 원장 gate 책임이다.
                attempt = attempts.get(request.chunk_id, 0) + 1
                attempts[request.chunk_id] = attempt

                def prepare(stage, payload, digest):
                    return prepare_attempt(source, intent, request, attempt, stage, payload, digest)

                scoped_transport = GatedResponsesTransport(bound_dispatch, prepare, post=post)
                scoped_provider = OpenAITranslationProvider(config, transport=scoped_transport)
                return scoped_provider.translate(request, current_settings)

        service = self.service
        scoped = TranslationService(CheckedProvider(), lambda: source.translation_revision_id,
            retry_policy=service.retry_policy, sleep=service.sleep, clock=service.clock,
            random_value=service.random_value, quality_retry_once=service.quality_retry_once)
        inputs = tuple(Chunk(c.chunk_id, c.parse_revision_id, c.chunk_index, c.original_text,
                             printed_page_label=c.printed_page_label, pdf_page=c.pdf_page,
                             section_id=c.section_id) for c in chunks)
        return scoped.translate(inputs, settings)
