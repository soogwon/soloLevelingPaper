"""게시 전에 하위 조각과 부모 청크 전체 집합의 대응을 확인한다."""

import re

from solo_leveling.domain.remote_processing import EmbeddingBatch, EmbeddingFragment, ProcessingError
from solo_leveling.domain.embedding_windows import TokenWindowPolicy


def validate_embedding_mapping(batch, chunk_ids, embedding_set_id):
    try:
        if (type(batch) is not EmbeddingBatch or batch.chunk_ids != chunk_ids
                or type(batch.chunk_ids) is not tuple or type(batch.vectors) is not tuple
                or type(batch.fragments) is not tuple):
            raise ValueError()
        if batch.split_policy is None:
            if batch.fragments or len(batch.vectors) != len(chunk_ids):
                raise ValueError()
            return
        policy = batch.split_policy
        expected = TokenWindowPolicy(policy['payload_tokens'], policy['overlap_tokens'],
                                     policy['version'])
        if (policy != expected.fingerprint() or len(batch.fragments) != len(batch.vectors)
                or not batch.fragments or expected.payload_tokens >= batch.spec.max_length):
            raise ValueError()
        groups = {chunk_id: [] for chunk_id in chunk_ids}
        seen = set()
        for fragment in batch.fragments:
            if (type(fragment) is not EmbeddingFragment or fragment.embedding_set_id != embedding_set_id
                    or fragment.parent_chunk_id not in groups
                    or type(fragment.embedding_fragment_id) is not str
                    or not re.fullmatch('[0-9a-f]{64}', fragment.embedding_fragment_id)
                    or fragment.embedding_fragment_id in seen
                    or any(type(v) is not int for v in
                           (fragment.token_start,fragment.token_end,fragment.parent_token_count))
                    or not 0 <= fragment.token_start < fragment.token_end <= fragment.parent_token_count):
                raise ValueError()
            seen.add(fragment.embedding_fragment_id)
            groups[fragment.parent_chunk_id].append(fragment)
        for group in groups.values():
            if not group:
                raise ValueError()
            size = group[0].parent_token_count
            count = 1 + (max(size - expected.payload_tokens, 0) + expected.stride - 1) // expected.stride
            if len(group) != count:
                raise ValueError()
            for index, fragment in enumerate(group):
                start = index * expected.stride
                if (fragment.parent_token_count != size or fragment.token_start != start
                        or fragment.token_end != min(start + expected.payload_tokens, size)):
                    raise ValueError()
    except Exception:
        raise ProcessingError('PROCESSING_EMBEDDING_INVALID') from None
