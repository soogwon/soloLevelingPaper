"""통합 테스트가 직접 소유한 외부 자원을 정리한다."""

from contextlib import ExitStack

import pytest

from solo_leveling.infrastructure.storage.vector_store import get_client


@pytest.fixture
def chroma_client_factory():
    """테스트가 만든 클라이언트만 성공·실패와 무관하게 종료한다."""
    with ExitStack() as cleanup:
        def create(persist_dir):
            client = get_client(persist_dir)
            cleanup.callback(client.close)
            return client

        yield create
