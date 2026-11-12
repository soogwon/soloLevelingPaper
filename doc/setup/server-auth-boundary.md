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

`OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`을 모두 설정하면 `AccessTokenVerifier` 포트에 OIDC RS256 adapter가 연결된다. adapter는 토큰이 선언한 알고리즘을 신뢰하지 않고 RS256만 허용하며, JWKS 서명과 `iss`, `aud`, `exp`, `iat`, `sub`를 검증한다. 설정이 없으면 보호 API는 계속 fail-closed로 동작하고, 일부 값만 설정되면 서버 시작을 거부한다.

OIDC 토큰의 기본 자격은 `web_session`이다. 연결된 로컬 기기 토큰에는 인증 제공자 또는 서버가 검증해 넣은 `credential_type=device`와 `device_id` claim이 모두 필요하다. 클라이언트가 임의로 보낸 claim은 유효한 서버 서명 없이는 사용할 수 없다.

다음으로 인증 제공자의 토큰 발급·폐기 방식과 기기 등록 모델을 연결한다. 이후 모든 논문·학습 기록 repository는 handler가 받은 임의 ID가 아니라 `AuthenticatedPrincipal.user_id`를 소유권 필터에 사용한다.

로컬 계정 연결은 일반 MCP tool이 아니라 전용 CLI와 시스템 브라우저의 Authorization Code + PKCE 흐름으로 구현한다. access token과 refresh token은 MCP 입력·출력, 일반 로그, 로컬 SQLite 본문에 저장하지 않는다.
