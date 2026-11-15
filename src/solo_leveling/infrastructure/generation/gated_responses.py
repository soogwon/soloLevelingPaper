"""원장 gate와 Responses의 물리 전송 1회를 연결하는 선택적 원격 adapter."""

from dataclasses import dataclass
import hashlib
import json

import requests

from solo_leveling.application.provider_dispatch import DispatchError, ProviderResponse


@dataclass(frozen=True, repr=False)
class PreparedProviderAttempt:
    dispatch_key: str
    payload_sha256: str
    input_token_upper_bound: int


def payload_digest(payload):
    try:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise DispatchError('DISPATCH_PAYLOAD_INVALID') from None


class GatedResponsesTransport:
    """prepare는 고정 payload의 안전한 상한/영속 dispatch key를 공급하는 신뢰 포트다.

    추정기/원장은 여기서 만들지 않는다. 기본 허용이나 자동 key 생성도 하지 않는다.
    post 대역이 없으면 requests.post 1회만 사용하며 redirect/재시도를 허용하지 않는다.
    """

    def __init__(self, dispatch, prepare, *, post=None):
        self.dispatch, self.prepare, self.post = dispatch, prepare, post

    def send(self, settings, payload, stage):
        if not settings.allow_external_api or settings.local_only:
            raise DispatchError('DISPATCH_DISABLED')
        # 원본 payload와 prepare 포트의 변이가 실제 전송 내용을 바꾸지 못하게 고정한다.
        if not isinstance(payload, dict):
            raise DispatchError('DISPATCH_PAYLOAD_INVALID')
        digest = payload_digest(payload)
        frozen = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        if (frozen.get('model') != settings.model or frozen.get('store') is not False
                or type(frozen.get('max_output_tokens')) is not int
                or frozen['max_output_tokens'] != settings.max_output_tokens):
            raise DispatchError('DISPATCH_PAYLOAD_INVALID')
        try:
            plan = self.prepare(stage, json.loads(json.dumps(frozen)), digest)
        except Exception:
            raise DispatchError('DISPATCH_PREPARATION_UNAVAILABLE') from None
        if not isinstance(plan, PreparedProviderAttempt) or plan.payload_sha256 != digest:
            raise DispatchError('DISPATCH_PAYLOAD_INVALID')

        def transmit(_):
            post = requests.post if self.post is None else self.post
            response = post('https://api.openai.com/v1/responses', json=frozen,
                headers={'Authorization': f'Bearer {settings.api_key}'},
                timeout=(5.0, settings.timeout_seconds), allow_redirects=False)
            try:
                if response.status_code != 200:
                    # HTTP 오류만으로 not_sent/환급을 단정하지 않는다.
                    raise DispatchError('DISPATCH_PROVIDER_UNCERTAIN')
                envelope = response.json()
                usage = envelope.get('usage') if isinstance(envelope, dict) else None
                return ProviderResponse(envelope,
                    usage.get('input_tokens') if isinstance(usage, dict) else None,
                    usage.get('output_tokens') if isinstance(usage, dict) else None)
            finally:
                response.close()

        return self.dispatch.send(stage, plan.input_token_upper_bound, frozen['max_output_tokens'],
            transmit, dispatch_key=plan.dispatch_key).payload
