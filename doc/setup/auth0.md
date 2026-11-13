# Auth0 OIDC 설정

Solo Leveling 서버는 Auth0 Custom API가 발급한 RS256 JWT access token을 검증합니다. ID token이나
Auth0 `/userinfo` 전용 opaque token을 API 자격으로 받지 않습니다.

## 1. Custom API

Auth0 Dashboard의 **Applications > APIs > Create API**에서 다음처럼 만듭니다.

- Name: `Solo Leveling Staging API`
- Identifier: `https://solo-leveling-staging.soogyung.com/api`
- Signing Algorithm: `RS256`
- Access Token Profile/Dialect: `RFC 9068 Profile` (`rfc9068_profile`)

Identifier는 실제 접속 주소일 필요는 없지만 만든 뒤 변경할 수 없습니다. 이 값을 Render의
`OIDC_AUDIENCE`와 로컬 연결의 `OIDC_AUTHORIZATION_AUDIENCE`에 똑같이 사용합니다. RFC 9068
profile은 access token의 JOSE `typ`을 `at+jwt`로 만들어 서버가 ID token과 구분할 수 있게 합니다.

## 2. External site용 Regular Web Application

**Applications > Applications > Create Application**에서 `Regular Web Applications` 유형으로
`Solo Leveling Staging Web`을 만듭니다.

- Allowed Callback URLs: `https://solo-leveling-staging.soogyung.com/v1/auth/callback`
- Allowed Web Origins: `https://solo-leveling-staging.soogyung.com`
- Allowed Logout URLs: `https://solo-leveling-staging.soogyung.com/`
- Token Endpoint Authentication Method: `Post` (`client_secret_post`)

서버는 Authorization Code + PKCE(S256)를 사용합니다. Application의 Domain, Client ID, Client
Secret을 Render 환경 변수에 입력하고 secret은 저장소나 로그에 남기지 않습니다.

## 3. 로컬 MCP용 Native Application

`Native` 유형으로 `Solo Leveling Local Connector`를 만듭니다.

- Allowed Callback URLs: `http://127.0.0.1:8765/callback`
- Token Endpoint Authentication Method: `None`
- Authorization Code와 PKCE(S256) 사용

Native client에는 client secret을 발급·배포하지 않습니다. Auth0는 callback을 정확히 비교하므로
로컬 연결 명령은 기본 고정 loopback 포트 `8765`를 열고 `audience`로 위 Custom API Identifier를
요청합니다. 포트가 이미 사용 중이면 Auth0 callback과 `OIDC_REDIRECT_PORT`를 함께 다른 값으로
바꿉니다. Auth0의 non-verifiable callback 확인 화면은 보안상 비활성화하지 않는 것을 권장합니다.

## 4. Render 환경 변수

Auth0 tenant domain이 `example.us.auth0.com`인 경우 다음처럼 입력합니다. Auth0가 표시한 tenant
domain을 사용하며 issuer의 마지막 `/`도 포함합니다.

```text
OIDC_ISSUER=https://example.us.auth0.com/
OIDC_AUDIENCE=https://solo-leveling-staging.soogyung.com/api
OIDC_JWKS_URL=https://example.us.auth0.com/.well-known/jwks.json
WEB_OIDC_AUTHORIZATION_URL=https://example.us.auth0.com/authorize
WEB_OIDC_TOKEN_URL=https://example.us.auth0.com/oauth/token
WEB_OIDC_USERINFO_URL=https://example.us.auth0.com/userinfo
WEB_OIDC_CLIENT_ID=<Regular Web Application Client ID>
WEB_OIDC_CLIENT_SECRET=<Regular Web Application Client Secret>
```

## 5. 발급 토큰 확인

테스트 로그인 후 access token은 최소한 다음 조건을 만족해야 합니다.

```json
{
  "header": {"alg": "RS256", "typ": "at+jwt", "kid": "..."},
  "payload": {
    "iss": "https://example.us.auth0.com/",
    "aud": "https://solo-leveling-staging.soogyung.com/api",
    "sub": "auth0|...",
    "iat": 0,
    "exp": 0
  }
}
```

`audience` 없이 로그인하면 Auth0가 opaque access token을 발급할 수 있습니다. 웹 로그인과 로컬
연결은 모두 Custom API Identifier를 명시적으로 보내도록 구성되어 있습니다.
