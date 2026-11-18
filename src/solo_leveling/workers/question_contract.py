"""일반 질문의 전송 모양·고정 단가·영속 예산을 검증한다. 실제 호출 승인은 별도다."""
from dataclasses import asdict, dataclass
import hashlib
import json

from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.infrastructure.generation.openai_generator import _INSTRUCTIONS, _claim_schema


def require(value):
    if not value:
        raise DispatchError('QUESTION_CONTRACT_REJECTED')


@dataclass(frozen=True)
class QuestionContract:
    model: str
    policy_version: str
    window_key: str
    estimator_revision: str
    price_revision: str
    max_input_tokens: int
    max_output_tokens: int
    max_payload_bytes: int
    input_token_usd_nanos: int
    output_token_usd_nanos: int
    question_usd_nanos: int
    owner_usd_nanos: int
    service_usd_nanos: int

    def __post_init__(self):
        for key, value in asdict(self).items():
            require(isinstance(value, str) and bool(value.strip()) if key in (
                'model', 'policy_version', 'window_key', 'estimator_revision', 'price_revision')
                else type(value) is int and 0 < value <= 2**63-1)
        require(self.question_usd_nanos <= self.owner_usd_nanos <= self.service_usd_nanos)
        require(self.cost(self.max_input_tokens, self.max_output_tokens) <= self.question_usd_nanos)

    @property
    def profile(self):
        # 단가·상한·계산기·프롬프트 변경은 기존 동의 snapshot으로 조용히 적용하지 않는다.
        pinned = {'contract': asdict(self), 'instructions': _INSTRUCTIONS, 'schema': _claim_schema([])}
        digest = hashlib.sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()
        return 'general-question-' + digest

    def cost(self, input_tokens, output_tokens):
        return input_tokens * self.input_token_usd_nanos + output_tokens * self.output_token_usd_nanos

    def validate_policy(self, policy, model, window, profile, semantic_enabled):
        require(policy.version == self.policy_version and model == self.model and window == self.window_key
                and profile == self.profile and not semantic_enabled and policy.max_calls == 1
                and policy.max_input_tokens == self.max_input_tokens
                and policy.max_output_tokens == self.max_output_tokens)

    def validate_payload(self, stage, payload):
        require(stage == 'generation' and type(payload) is dict and set(payload) == {
            'model', 'instructions', 'input', 'text', 'max_output_tokens', 'store'})
        require(payload['model'] == self.model and payload['instructions'] == _INSTRUCTIONS
                and payload['store'] is False and type(payload['max_output_tokens']) is int
                and payload['max_output_tokens'] == self.max_output_tokens)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        require(len(encoded) <= self.max_payload_bytes)
        inputs = payload['input']
        require(type(inputs) is list and len(inputs) == 1 and type(inputs[0]) is dict
                and set(inputs[0]) == {'role', 'content'} and inputs[0]['role'] == 'user'
                and type(inputs[0]['content']) is str)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result)
                result[key] = value
            return result
        data = json.loads(inputs[0]['content'], object_pairs_hook=unique)
        require(type(data) is dict and set(data) == {'question', 'evidence'}
                and type(data['question']) is str and bool(data['question'].strip())
                and type(data['evidence']) is list and bool(data['evidence']))
        ids = []
        for row in data['evidence']:
            require(type(row) is dict and set(row) == {'evidence_id', 'text_ko', 'original_text',
                'starts_mid_sentence', 'ends_mid_sentence', 'follows_evidence_id'})
            require(type(row['evidence_id']) is str and bool(row['evidence_id'])
                    and type(row['original_text']) is str and bool(row['original_text'])
                    and (row['text_ko'] is None or type(row['text_ko']) is str)
                    and type(row['starts_mid_sentence']) is bool and type(row['ends_mid_sentence']) is bool
                    and (row['follows_evidence_id'] is None or type(row['follows_evidence_id']) is str))
            ids.append(row['evidence_id'])
        require(len(ids) == len(set(ids)))
        require(payload['text'] == {'format': {'type': 'json_schema', 'name': 'evidence_claims',
            'strict': True, 'schema': _claim_schema(ids)}})

    def validate_upper(self, upper):
        require(type(upper) is int and 0 < upper <= self.max_input_tokens
                and self.cost(upper, self.max_output_tokens) <= self.question_usd_nanos)

    def validate_limits(self, limits):
        """원장은 토큰으로 정산한다. 잠긴 전체 상한의 최악 비용이 승인액 이하인지 검사한다."""
        require(len(limits) == 2 and {r['scope_type'] for r in limits} == {'owner', 'service'})
        for row in limits:
            tokens = [row['max_input_tokens'], row['max_output_tokens']]
            require(all(type(v) is int and v > 0 for v in tokens))
            cap = self.owner_usd_nanos if row['scope_type'] == 'owner' else self.service_usd_nanos
            require(self.cost(*tokens) <= cap)
        return True
