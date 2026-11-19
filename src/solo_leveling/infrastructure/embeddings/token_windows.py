"""고정 tokenizer의 토큰 구간을 분할한다. 원문 청크와 인용 식별자는 바꾸지 않는다."""

import hashlib
import json


from solo_leveling.domain.embedding_windows import (
    TokenWindow as TokenWindow, TokenWindowError as TokenWindowError, TokenWindowPolicy as TokenWindowPolicy,
)


def token_ids(tokenizer, text, *, special_tokens):
    try:
        if not isinstance(text, str) or not text.strip():
            raise ValueError()
        # tokenize()/encode()의 기본 truncation을 사용하지 않는다.
        ids = tokenizer.encode(text, add_special_tokens=special_tokens, truncation=False)
        if (type(ids) is not list or not ids
                or any(type(value) is not int or value < 0 for value in ids)):
            raise ValueError()
        return tuple(ids)
    except Exception:
        raise TokenWindowError('EMBEDDING_TOKENIZATION_FAILED') from None


def require_fits(tokenizer, text, max_length):
    if type(max_length) is not int or max_length <= 0:
        raise TokenWindowError('EMBEDDING_WINDOW_POLICY_INVALID')
    if len(token_ids(tokenizer, text, special_tokens=True)) > max_length:
        raise TokenWindowError('EMBEDDING_INPUT_TOO_LONG')


def split_token_windows(tokenizer, text, *, parent_chunk_id, embedding_set_id,
                        max_length, policy=TokenWindowPolicy()):
    """끝 구간까지 빠짐없이 포함한다. 반환 토큰은 재해독·재토큰화하지 않는다."""
    if (type(policy) is not TokenWindowPolicy
            or any(not isinstance(value, str) or not value.strip()
                   for value in (parent_chunk_id, embedding_set_id))
            or type(max_length) is not int or max_length <= 0):
        raise TokenWindowError('EMBEDDING_WINDOW_POLICY_INVALID')
    try:
        special = tokenizer.num_special_tokens_to_add(pair=False)
    except Exception:
        raise TokenWindowError('EMBEDDING_TOKENIZATION_FAILED') from None
    if (type(special) is not int or special < 0
            or policy.payload_tokens + special > max_length):
        raise TokenWindowError('EMBEDDING_WINDOW_POLICY_INVALID')
    ids = token_ids(tokenizer, text, special_tokens=False)
    result = []
    for start in range(0, len(ids), policy.stride):
        end = min(start + policy.payload_tokens, len(ids))
        identity = [embedding_set_id, parent_chunk_id, policy.fingerprint(), start, end, ids[start:end]]
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        result.append(TokenWindow(digest, embedding_set_id, parent_chunk_id, start, end, ids[start:end]))
        if end == len(ids):
            break
    return tuple(result)
