# 중앙 서버 인증·소유권 경계

이 단계는 특정 인증 제공자를 선택하기 전에 중앙 API가 신뢰할 수 있는 인증 주체의 형태와 실패 동작을 고정한다. 로컬 MCP 자체를 원격 MCP로 바꾸거나 계정 로그인 UI를 구현하는 단계가 아니다.

## 인증 주체

서버는 검증된 access token에서만 다음 내부 정보를 만든다.

- `user_id`: 서버 객체 소유권을 검사하는 내부 사용자 식별자
- `credential_type`: `web_session` 또는 `device`
- `device_id`: 연결된 로컬 기기 자격에만 존재하는 서버 기기 식별자

요청 본문, query string, 임의 헤더에 포함된 `user_id`는 인증 또는 소유권 판단에 사용하지 않는다. 웹 세션에는 `device_id`를 붙이지 않고, 기기 자격에는 반드시 `device_id`가 있어야 한다.

## HTTP 계약

- `GET /health`는 인증 없이 프로세스 상태를 확인한다.
- `GET /v1/session`은 `Authorization: Bearer <access token>`이 필요하다.
- 토큰이 없거나 만료·폐기·위조된 경우 모두 `401`과 `WWW-Authenticate: Bearer`를 반환한다.
- 인증 실패 응답은 사용자나 기기의 존재 여부, 실패 세부 원인, 토큰 내용을 노출하지 않는다.
- 인증 제공자가 연결되지 않은 기본 서버는 모든 보호 요청을 거부한다.

`GET /v1/session` 응답은 내부 `user_id`를 반환하지 않는다. 웹 또는 로컬 클라이언트가 자격 종류와 기기 연결 여부를 안전하게 확인하기 위한 최소 응답만 제공한다.

## 다음 구현

인증 제공자를 선택한 뒤 `AccessTokenVerifier` 포트에 issuer, audience, 서명, 만료와 폐기 상태를 검증하는 adapter를 연결한다. 이후 모든 논문·학습 기록 repository는 handler가 받은 임의 ID가 아니라 `AuthenticatedPrincipal.user_id`를 소유권 필터에 사용한다.

로컬 계정 연결은 일반 MCP tool이 아니라 전용 CLI와 시스템 브라우저의 Authorization Code + PKCE 흐름으로 구현한다. access token과 refresh token은 MCP 입력·출력, 일반 로그, 로컬 SQLite 본문에 저장하지 않는다.
