"""#390 서버 포트를 조립하되 실제 자료 도구를 공개하지 않는다."""

from solo_leveling.application.evidence_qa.remote_search import FingerprintCheckedEmbedder, RemoteContextSearchService
from solo_leveling.interfaces.http.remote_identities import PostgresRemoteOwnerResolver
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_vectors import PostgresRemoteScopedSearch
from solo_leveling.interfaces.mcp.remote_context import resolve_authenticated_context


def create_remote_search_service(connection_pool, embedding_runtime):
    owners = PostgresRemoteOwnerResolver(connection_pool)
    contexts = PostgresRemoteContextResolver(connection_pool)
    retriever = PostgresRemoteScopedSearch(connection_pool, FingerprintCheckedEmbedder(embedding_runtime))
    return RemoteContextSearchService(
        lambda context_id: resolve_authenticated_context(context_id, owners, contexts), retriever)
