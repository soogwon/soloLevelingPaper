# 외부 웹 로그인과 서버 세션

외부 웹은 OIDC Authorization Code + PKCE로 로그인한 뒤 API 서버의 세션 쿠키를 사용한다.
로컬 MCP의 공개 OIDC client와 달리, 웹 client secret과 토큰 교환은 서버 안에만 둔다.

## 운영 설정

`.env.example`의 필수 `WEB_OIDC_*` 여섯 값과 `WEB_APP_ORIGIN`을 모두 설정한다. OIDC 공급자에는
`WEB_OIDC_REDIRECT_URI`와 정확히 같은 HTTPS callback을 등록한다. `WEB_APP_ORIGIN`은 경로가
없는 외부 사이트 origin이며 credentialed CORS 허용 목록에도 이 값 하나만 사용된다.

프런트가 CSRF 쿠키를 읽어 헤더로 돌려보내므로 외부 사이트와 callback은 같은 origin이어야
한다. 외부 사이트의 `/v1`을 API 서버로 reverse proxy하고(예: 둘 다 `app.example.com`),
`WEB_OIDC_REDIRECT_URI=https://app.example.com/v1/auth/callback`로 설정한다. 서버는 이 조건을
시작할 때 검증한다.

## 흐름

1. 브라우저가 `GET /v1/auth/login?provider=oidc&return_to=<외부 사이트 URL>`을 연다.
2. 서버는 허용 origin을 확인하고 일회용 state와 PKCE verifier를 DB에 저장한 뒤 공급자로 보낸다.
3. `GET /v1/auth/callback`은 state를 한 번만 소비하고 code를 교환한다. 검증된 access token의
   subject와 userinfo subject가 같을 때만 세션을 만든다.
4. 응답은 `slp_session` HttpOnly 쿠키와 double-submit용 `csrf_token` 쿠키를 발급한다. DB에는
   두 값의 SHA-256 hash만 저장한다.
5. 브라우저는 `GET /v1/me`, `GET /v1/papers` 등에 쿠키를 보낸다. 상태 변경 요청은
   `csrf_token` 쿠키 값을 `X-CSRF-Token` 헤더에도 실어 보낸다.
6. `POST /v1/auth/logout`은 세션을 폐기하고 두 쿠키를 삭제한다.

로그인 시도는 10분, 웹 세션은 7일 후 만료된다. 세션 저장소나 OIDC 공급자가 응답하지 않으면
인증 endpoint는 fail-closed로 503을 반환한다.
