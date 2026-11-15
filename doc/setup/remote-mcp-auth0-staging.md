# 원격 MCP Auth0 스테이징 준비 — #388

#413의 제품 HTTP 스파이크와 #409 공개 경계 위에 적용한다. 별도 합성 서버나 인증기를
중복 배포하지 않는다. 실제 Auth0·Claude 연결은 미검증이며 #388/#77을 종료하지 않는다.

## 설정 순서

1. `REMOTE_MCP_SPIKE_ENABLED=false`를 유지한 채 보완본을 배포한다.
2. A가 확인한 전용 `REMOTE_MCP_OIDC_ISSUER`, `REMOTE_MCP_OIDC_JWKS_URL`을 환경 설정에 넣는다.
   입력은 신뢰하는 Auth0 값이어야 하며 토큰의 jku/x5u에서 가져오지 않는다.
3. `REMOTE_MCP_RESOURCE_URL`과 `REMOTE_MCP_OIDC_AUDIENCE`는 모두
   `https://solo-leveling-staging.soogyung.com/mcp`로 일치시킨다.
4. Claude published identity의 Auth0 호환성과 실제 client를 먼저 확인한다. 지원되지 않으면
   오류 증거를 남긴 뒤 직접 등록 또는 DCR을 비교한다. DCR을 미리 공개하거나 기존 웹/M2M client를 재사용하지 않는다.
5. 확인된 client ID만 `REMOTE_MCP_ALLOWED_CLIENT_IDS`에 쉼표로 구분해 넣는다.
   이 설정은 비밀이 아닌 client 식별자이며 client secret이나 토큰을 넣지 않는다.
   Auth0 쪽에서도 해당 client에 사용자 위임 `spike:read`만 허용한다.
6. `REMOTE_MCP_REQUIRED_SCOPE=spike:read`, 공개 Host와 실제 Origin 목록을 확인한다.
   [Host·프록시 경계](remote-mcp-public-boundary.md)를 먼저 검증한다.
7. 배포 담당자가 승인된 스테이징에서만 플래그를 true로 바꾸고 재배포한다.

기존 제품 HTTP 실행 명령과 /health·웹 API를 유지한다. 별도 mini-project 앱으로 제품
프로세스를 교체하지 않는다. 플래그를 false로 돌려 재배포하면 MCP 경로가 닫힌다.
임시 Auth0 client와 권한 회수는 설정 담당자가 별도로 확인한다.

## 인증 계약

- Auth0 JWT 프로필의 JWT/RS256, 신뢰 issuer/JWKS, 전용 audience, azp client 허용 목록을 검사한다.
- ID token(client audience), 기존 웹 audience, 기기 claim, M2M gty와 client subject를 거부한다.
- exp/iat는 필수, nbf는 존재하면 검사한다. NumericDate는 정수만 허용한다.
- 기본 clock skew는 0초다. 별도 설정을 늘려도 SDK의 expires_at 재검사는 만료 후 토큰을 허용하지 않는다.
- subject는 issuer와 결합한 내부 값이며 whoami·로그에 출력하지 않는다.
- JWKS는 별도 스레드에서 timeout 5초로 조회한다. 키 교체는 PyJWT의 key refresh 경로를 사용한다.
  조회 실패는 원본 오류를 숨기고 401로 닫힌 상태로 실패한다. 운영 장애와 무효 토큰을 HTTP로 구분하는 기능은 없다.
- scope가 부족해도 인증기가 거부하여 현재 제품 응답은 401이다. 이 동작을 실제 연결 표에 기록한다.
- refresh 회전·폐기는 Auth0/Claude의 실제 검증 대상이다. 서명 토큰의 즉시 폐기 조회를 제공하지 않으며,
  이미 발급된 access token의 남은 유효기간을 폐기 성공으로 혼동하지 않는다.
- stateless이며 모든 Mcp-Session-Id를 거부한다. 제품 세션 저장·다중 인스턴스는 #399 범위다.

참고: [Auth0 프로필과 azp](https://auth0.com/blog/jwt-access-tokens-profiles-now-in-ga/).
기존 웹 OIDC의 at+jwt 계약을 완화하지 않고 별도 원격 검증기를 사용한다.

## 배포 후 별도 검증

- 공개 HTTPS 443에서 metadata resource와 Claude 등록 /mcp URL 일치.
- 무토큰·만료·다른 issuer/audience/client·ID token·웹/기기/M2M 거부.
- 정상 initialize → tools/list → whoami. 목록에는 whoami/probe_card/probe_delay만 존재.
- 두 테스트 계정의 로그인·갱신·재연결, refresh 회전·재사용 거부·폐기.
- Claude 웹/Windows/macOS에서 카드 또는 텍스트 대체 확인. 5초 지연 테스트는 300초 경계 검증이 아니다.
- 프록시·서버 로그에서 토큰·개인정보·본문 비노출 확인. 합성 요청·데이터만 사용한다.
- 날짜·OS·호스트/요금제·버전·서버 SHA·설정 범위·기대/실제 결과와 미검증 항목 기록.

## 자동 검증의 범위

RS256 키를 테스트 중 생성하고 JWKS는 대역으로 제공한다. 실제 서명 검증, 키 선택/교체,
조회 실패, 제품 FastAPI→SDK→인증→whoami, 공개 Host 경계와 기존 웹 OIDC를 검사한다.
이는 실제 Auth0 설정·네트워크·Render·Claude 검증이 아니다. 현재 실제 배포·설정 변경은 하지 않았다.
