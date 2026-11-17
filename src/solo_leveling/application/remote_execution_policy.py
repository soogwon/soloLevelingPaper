"""서버가 접수 시 고정하는 원격 질문 실행 정책. 운영 기본값은 두지 않는다."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RemoteExecutionPolicy:
    version: str
    timeout_seconds: int
    stage_timeout_seconds: int
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int

    def __post_init__(self):
        if (not isinstance(self.version, str) or not self.version.strip()
                or any(type(v) is not int or not 1 <= v <= 2147483647
                       for k, v in asdict(self).items() if k != 'version')
                or self.stage_timeout_seconds > self.timeout_seconds):
            raise ValueError('remote_execution_policy_invalid')

    def snapshot(self):
        return asdict(self)
