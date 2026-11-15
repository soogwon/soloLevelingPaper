"""실제 서명 검증 뒤 SDK 요청 컨텍스트에서 owner/context 대역으로 연결한다."""

import asyncio
from dataclasses import replace
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
import pytest

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding, RemoteRetrievalContractError
from solo_leveling.interfaces.http.remote_mcp_auth import Auth0McpTokenVerifier, RemoteIdentityAccessToken
from solo_leveling.interfaces.http.settings import RemoteMcpSettings
from solo_leveling.interfaces.mcp.remote_context import resolve_authenticated_context

ISSUER = 'https://identity.example/'
RESOURCE = 'https://service.example/mcp'
SETTINGS = RemoteMcpSettings(enabled=True, issuer=ISSUER, audience=RESOURCE, resource_url=RESOURCE,
    jwks_url=ISSUER+'jwks', allowed_hosts=('service.example',), allowed_client_ids=('client-a',))
BINDING = RemoteSearchBinding('owner-a', 'context-a', 'paper-a', 'version-a', 'old-publication',
    'parse-a', 'translation-a', 'set-a', '{}')


@pytest.fixture
def verified():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key()))
    def make(subject='raw#subject', **changes):
        claims = dict(iss=ISSUER, aud=RESOURCE, sub=subject, iat=int(time.time()),
                      exp=int(time.time())+300, scope='spike:read', azp='client-a')
        claims.update(changes)
        signed = jwt.encode(claims, private, algorithm='RS256', headers={'kid':'test','typ':'JWT'})
        return asyncio.run(Auth0McpTokenVerifier(SETTINGS, keys).verify_token(signed))
    return make


class Owners:
    def __init__(self): self.seen = []; self.owner = 'owner-a'
    def resolve_owner(self, identity):
        self.seen.append(identity)
        return self.owner


class Contexts:
    def __init__(self): self.seen = []; self.binding = BINDING
    def resolve(self, owner, context):
        self.seen.append((owner, context))
        return self.binding


def resolve(token, owners, contexts):
    marker = auth_context_var.set(AuthenticatedUser(token) if token else None)
    try:
        return resolve_authenticated_context('context-a', owners, contexts)
    finally:
        auth_context_var.reset(marker)


def test_verified_raw_subject_and_past_publication_are_preserved(verified):
    token = verified()
    owners, contexts = Owners(), Contexts()
    assert resolve(token, owners, contexts) == BINDING
    assert owners.seen[0].issuer == ISSUER
    assert owners.seen[0].subject == 'raw#subject'
    assert contexts.seen == [('owner-a', 'context-a')]
    assert '_verified_identity' not in token.model_dump()
    assert '_verified_identity' not in repr(token)


def test_serialized_token_cannot_restore_verified_identity(verified):
    token = RemoteIdentityAccessToken(**verified().model_dump())
    owners, contexts = Owners(), Contexts()
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_AUTHENTICATION_REQUIRED'):
        resolve(token, owners, contexts)
    assert not owners.seen and not contexts.seen


@pytest.mark.parametrize('kind', ['none', 'generic', 'expired'])
def test_unverified_or_expired_request_never_queries_owner(verified, kind):
    token = verified()
    if kind == 'none': token = None
    if kind == 'generic': token = AccessToken(**token.model_dump())
    if kind == 'expired': token.expires_at = int(time.time())-1
    owners, contexts = Owners(), Contexts()
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_AUTHENTICATION_REQUIRED'):
        resolve(token, owners, contexts)
    assert not owners.seen and not contexts.seen


def test_missing_or_revoked_mapping_stops_context_lookup(verified):
    owners, contexts = Owners(), Contexts()
    owners.owner = None
    assert resolve(verified(), owners, contexts) is None
    assert not contexts.seen


def test_mapping_is_rechecked_on_every_request(verified):
    token = verified()
    owners, contexts = Owners(), Contexts()
    assert resolve(token, owners, contexts) == BINDING
    owners.owner = None
    assert resolve(token, owners, contexts) is None
    assert len(owners.seen) == 2 and len(contexts.seen) == 1


def test_mapping_failure_redacts_and_stops_context_lookup(verified, caplog):
    class Broken:
        def resolve_owner(self, identity): raise RuntimeError('private-owner-detail')
    contexts = Contexts()
    with pytest.raises(RemoteRetrievalContractError, match='^REMOTE_OWNER_MAPPING_UNAVAILABLE$'):
        resolve(verified(), Broken(), contexts)
    assert not contexts.seen and 'private-owner-detail' not in caplog.text


def test_wrong_owner_binding_is_rejected(verified):
    contexts = Contexts()
    contexts.binding = replace(BINDING, owner_user_id='owner-b')
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_SCOPE_MISMATCH'):
        resolve(verified(), Owners(), contexts)


@pytest.mark.parametrize('claims', [{'aud':'wrong'}, {'azp':'other-client'}, {'sub':' padded '}, {'sub':'x'*513}])
def test_rejected_token_carries_no_identity(verified, claims):
    assert verified(**claims) is None
