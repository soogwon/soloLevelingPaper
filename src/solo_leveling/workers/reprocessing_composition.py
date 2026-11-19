"""기존 OpenAI 번역 어댑터를 복구 전용 영속 전송 gate에 연결한다."""
from solo_leveling.infrastructure.generation.gated_responses import (
    GatedResponsesTransport, PreparedProviderAttempt,
)
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationProvider
from .verification_bootstrap import ApprovedPayloadBounds
from .remote_reprocessing import require


def translation_factory(config, approved_requests, *, post=None):
    """정확한 payload 해시와 입력 상한이 없는 요청은 전송하지 않는다."""
    require(config.allow_external_api and not config.local_only)

    def factory(worker, contract):
        require(contract['provider']==config.translation_settings.provider
                and contract['model']==config.translation_settings.model
                and config.timeout_seconds+5<worker.approval.lease_seconds)
        bounds=ApprovedPayloadBounds(contract['model'],config.max_output_tokens,approved_requests)
        require(bounds.max_requests<=contract['max_calls'] and bounds.max_input<=contract['max_input_tokens']
                and bounds.max_output<=contract['max_output_tokens'])

        class Dispatch:
            def send(self, stage, input_upper, output_upper, transmit, *, dispatch_key):
                require(stage=='translation')
                return worker.dispatch(dispatch_key,input_upper,output_upper,lambda:transmit(None))

        def prepare(stage,payload,digest):
            require(stage=='translation')
            return PreparedProviderAttempt(digest,digest,bounds(contract['model'],payload))

        provider=OpenAITranslationProvider(config,transport=GatedResponsesTransport(Dispatch(),prepare,post=post))
        return lambda request:provider.translate(request,config.translation_settings)
    return factory
