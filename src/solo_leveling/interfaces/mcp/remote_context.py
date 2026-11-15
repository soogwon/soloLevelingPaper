"""#390 인증된 요청의 identity에서만 원격 context를 해석한다. 제품 도구 등록은 별도다."""

import time

from mcp.server.auth.middleware.auth_context import get_access_token

from solo_leveling.application.evidence_qa.remote_contract import (
    RemoteContextResolver, RemoteOwnerResolver, RemoteRetrievalContractError, resolve_remote_binding,
)
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken


def resolve_authenticated_context(context_id: str, owners: RemoteOwnerResolver, contexts: RemoteContextResolver):
    """SDK 요청 컨텍스트만 사용한다. owner/identity/토큰을 도구 입력에서 받지 않는다."""
    token = get_access_token()
    if (not isinstance(token, RemoteIdentityAccessToken)
            or token.verified_identity() is None
            or type(token.expires_at) is not int or token.expires_at <= time.time()):
        raise RemoteRetrievalContractError('REMOTE_AUTHENTICATION_REQUIRED')
    return resolve_remote_binding(token.verified_identity(), context_id, owners, contexts)
