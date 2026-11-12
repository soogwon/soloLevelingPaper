# 로컬 설치의 중앙 서버 연결

로컬 MCP의 계정 연결은 일반 MCP tool이 아니라 `solo-leveling-connect` CLI에서 수행한다. CLI는 시스템 브라우저와 Authorization Code + PKCE의 `S256` 방식만 사용하며 OAuth client secret을 로컬 앱에 포함하지 않는다.

## 인증 제공자 설정

인증 제공자에는 public/native client와 `http://127.0.0.1:{임의 포트}/callback` 형태의 loopback redirect를 허용해야 한다. API용 access token은 서버 인증 계약의 issuer, audience, `typ=at+jwt` 요구를 충족해야 한다.

```powershell
$env:OIDC_AUTHORIZATION_URL = "https://identity.example.com/authorize"
$env:OIDC_TOKEN_URL = "https://identity.example.com/token"
$env:OIDC_CLIENT_ID = "public-client-id"
$env:OIDC_CLIENT_SCOPES = "openid profile"
$env:OIDC_AUTHORIZATION_AUDIENCE = "solo-leveling-api"
$env:SERVER_API_URL = "https://api.example.com"

solo-leveling-connect --device-name "My PC"
```

authorization, token, 서버 API URL은 HTTPS만 허용한다. OAuth callback만 OS가 할당한 `127.0.0.1` 임시 포트의 HTTP를 사용한다. `state`가 일치하지 않는 callback은 무시하며 code verifier는 요청마다 새로 생성한다.

## 자격 보관

로그인이 끝나면 CLI는 웹 access token으로 기기를 등록하고 서버 기기 access/refresh token을 발급받는다. 토큰은 stdout, URL, MCP 응답 또는 로컬 SQLite에 기록하지 않고 운영체제 자격 증명 저장소에 JSON 한 건으로 보관한다.

macOS는 Keychain, Windows는 자격 증명 관리자, Linux는 Secret Service를 사용한다. 해당 저장소를 이용할 수 없으면 연결에 실패한다.

기기 등록 후 자격 발급 또는 저장에 실패하면 방금 등록한 서버 기기를 best-effort로 해제한다. 해제까지 실패할 수 있으므로 사용자는 외부 웹의 기기 목록에서 남은 기기를 확인하고 직접 해제해야 한다.

`AuthenticatedServerClient`는 keyring의 access token을 서버 요청에 사용한다. access token 만료 30초 전 또는 서버의 401 응답 시 refresh를 한 번 수행하며, 회전된 access/refresh token을 keyring의 단일 레코드로 교체한다. 같은 프로세스의 여러 client가 동시에 갱신해도 한 번만 회전한다. 한 요청에서 갱신한 자격으로 다시 401을 받거나 대기 중 연결 기기가 바뀌면 재연결이 필요하다.

refresh token이 만료·거부되거나 회전 응답의 기기 ID·만료 경계가 기존 자격과 맞지 않으면 새 값을 저장하지 않고 재연결 필요 상태로 처리한다. redirect를 따라가지 않으며 호출 경로는 같은 서버의 `/v1/` 아래로 제한한다.

다음 단계에서는 이 client를 논문 메타데이터 동기화 서비스에 주입하고, 로컬 ID mapping과 outbox를 구현한다.
