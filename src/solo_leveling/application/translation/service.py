"""Translate a single parse revision in memory while preserving input chunks."""

from dataclasses import replace
import json
import random
import sys
import time
from typing import Callable, Sequence
from uuid import uuid4

from solo_leveling.domain.models import Chunk, TranslationRevision
from solo_leveling.domain.translation import (
    ChunkTranslationResult, TranslationBatchResult, TranslationFailureCode,
    TranslationRequest, TranslationSettings, require_text,
)
from .ports import TranslationProvider, TranslationProviderUnavailable
from .retry import RetryBudget, TranslationRetryPolicy


class TranslationService:
    def __init__(
        self, provider: TranslationProvider,
        revision_id_factory: Callable[[], str] | None = None,
        *, retry_policy: TranslationRetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self.provider = provider
        self.revision_id_factory = revision_id_factory or (lambda: str(uuid4()))
        if retry_policy is not None and not isinstance(retry_policy, TranslationRetryPolicy):
            raise ValueError('올바른 번역 재시도 정책이 필요합니다.')
        self.retry_policy = retry_policy if retry_policy is not None else TranslationRetryPolicy()
        self.sleep, self.clock, self.random_value = sleep, clock, random_value

    def with_revision_id(self, revision_id: str) -> 'TranslationService':
        """기존 리비전 재사용 시 재시도 설정과 테스트용 의존성을 보존한다."""
        require_text(revision_id, 'translation_revision_id')
        return TranslationService(self.provider, lambda: revision_id,
            retry_policy=self.retry_policy, sleep=self.sleep, clock=self.clock,
            random_value=self.random_value)

    def _translate_one(self, request: TranslationRequest, settings: TranslationSettings,
                       budget: RetryBudget, batch_id: str, chunk_number: int) -> str:
        policy = self.retry_policy
        started = self.clock()
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return self.provider.translate(request, settings)
            except TranslationProviderUnavailable as error:
                delay = 0.0
                retry = (error.retryable and attempt < policy.max_attempts
                         and budget.retries < policy.max_batch_retries)
                if retry:
                    delay = policy.base_delay_seconds * 2 ** (attempt - 1)
                    delay += policy.jitter_seconds * min(1.0, max(0.0, self.random_value()))
                    delay = max(delay, error.retry_after_seconds or 0.0)
                    now = self.clock()
                    retry = (delay <= policy.max_delay_seconds
                             and budget.waited + delay <= policy.max_batch_wait_seconds
                             and now - started + delay < policy.chunk_retry_window_seconds
                             and now - budget.started + delay < policy.batch_retry_window_seconds)
                # 사용자 입력과 외부 예외 문자열 없이 내부 식별자·고정 코드만 남긴다.
                try:
                    print(json.dumps({'event': 'translation_failure', 'batch_id': batch_id,
                        'chunk_number': chunk_number, 'attempt': attempt,
                        'code': error.code.value, 'retry': retry,
                        'delay_seconds': delay if retry else 0.0}), file=sys.stderr, flush=True)
                except (OSError, ValueError):
                    pass
                if not retry:
                    raise
                before = self.clock()
                self.sleep(delay)
                budget.waited += max(delay, self.clock() - before)
                # 스케줄링으로 대기가 길어진 경우에도 기한을 넘겨 새 호출하지 않는다.
                now = self.clock()
                if (now - started >= policy.chunk_retry_window_seconds
                        or now - budget.started >= policy.batch_retry_window_seconds
                        or budget.waited > policy.max_batch_wait_seconds):
                    raise
                budget.retries += 1

    def translate(
        self, chunks: Sequence[Chunk], settings: TranslationSettings,
    ) -> TranslationBatchResult:
        if not isinstance(settings, TranslationSettings):
            raise ValueError("settings must be TranslationSettings")
        inputs = tuple(chunks)
        if not inputs or any(not isinstance(c, Chunk) for c in inputs):
            raise ValueError("a nonempty sequence of Chunk is required")
        snapshots = tuple(replace(c) for c in inputs)
        # Validate the entire batch before the first provider call.
        requests = tuple(TranslationRequest(
            c.chunk_id, c.parse_revision_id, c.original_text, settings.target_language,
        ) for c in snapshots)
        parse_id = requests[0].parse_revision_id
        if any(r.parse_revision_id != parse_id for r in requests):
            raise ValueError("all chunks must belong to one parse revision")
        if len({r.chunk_id for r in requests}) != len(requests):
            raise ValueError("duplicate chunk ID")
        revision_id = self.revision_id_factory()
        require_text(revision_id, "translation_revision_id")
        if any(c.translation_revision_id == revision_id for c in snapshots):
            raise ValueError("retranslation requires a new revision ID")
        revision = TranslationRevision(revision_id, parse_id, settings.provider, settings.model)
        results = []
        budget = RetryBudget(self.clock())
        batch_id = str(uuid4())
        for chunk_number, request in enumerate(requests):
            try:
                text = self._translate_one(request, settings, budget, batch_id, chunk_number)
            except TranslationProviderUnavailable:
                results.append(ChunkTranslationResult(
                    request, None, TranslationFailureCode.PROVIDER_UNAVAILABLE,
                ))
                continue
            if not isinstance(text, str):
                raise TypeError("translation provider must return str")
            if not text.strip():
                results.append(ChunkTranslationResult(
                    request, None, TranslationFailureCode.EMPTY_TRANSLATION,
                ))
            else:
                results.append(ChunkTranslationResult(request, text))
        return TranslationBatchResult(revision, settings, tuple(results))
