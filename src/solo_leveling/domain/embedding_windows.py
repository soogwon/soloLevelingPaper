"""임베딩 전용 토큰 구간과 분할 정책. 원본 청크 식별자를 유지한다."""

from dataclasses import asdict, dataclass


class TokenWindowError(ValueError):
    """본문이나 tokenizer 예외를 포함하지 않는 고정 오류."""


@dataclass(frozen=True)
class TokenWindowPolicy:
    payload_tokens: int = 112
    overlap_tokens: int = 24
    version: str = 'token-window-v1'

    def __post_init__(self):
        if (type(self.payload_tokens) is not int or self.payload_tokens <= 0
                or type(self.overlap_tokens) is not int
                or not 0 <= self.overlap_tokens < self.payload_tokens
                or self.version != 'token-window-v1'):
            raise TokenWindowError('EMBEDDING_WINDOW_POLICY_INVALID')

    @property
    def stride(self):
        return self.payload_tokens - self.overlap_tokens

    def fingerprint(self):
        return {**asdict(self), 'stride_tokens': self.stride}


@dataclass(frozen=True, repr=False)
class TokenWindow:
    embedding_fragment_id: str
    embedding_set_id: str
    parent_chunk_id: str
    token_start: int
    token_end: int
    token_ids: tuple[int, ...]


