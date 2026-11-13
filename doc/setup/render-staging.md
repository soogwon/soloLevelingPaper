# Render 스테이징 배포

이 구성은 하나의 Render Web Service가 FastAPI와 `web/` 정적 파일을 같은 origin으로 제공합니다.
브라우저의 `/v1` 요청과 OIDC 세션 쿠키가 별도 CORS·쿠키 예외 없이 동작하도록 하기 위한 구성입니다.

## 저장소에 포함된 배포 구성

- `render.yaml`: 무료 Web Service와 무료 Render Postgres Blueprint
- `Dockerfile`: 서버 의존성과 API·웹 파일을 포함한 이미지
- `deploy/render-start.sh`: 서버 시작 전 PostgreSQL 마이그레이션 실행
- `WEB_STATIC_ROOT=/app/web`: API route 등록 후 외부 사이트를 `/`에 mount
- Render가 주입하는 `PORT`: `API_PORT`가 없을 때 자동 사용

무료 Web Service에는 Render의 pre-deploy command가 제공되지 않습니다. 따라서 스테이징의 단일 인스턴스가
시작할 때 마이그레이션을 먼저 실행합니다. 마이그레이션은 PostgreSQL advisory lock과 적용 이력으로
중복 실행을 방지합니다. 나중에 유료 Web Service로 전환하면 Dashboard의 Pre-Deploy Command를
`solo-leveling-server-migrate`로 지정하고 시작 명령을 `solo-leveling-api`로 분리하는 편이 좋습니다.

## 최초 배포

1. Render Dashboard에서 **New > Blueprint**를 선택하고 이 GitHub 저장소를 연결합니다.
2. Blueprint 경로로 저장소 루트의 `render.yaml`을 사용합니다.
3. 생성 화면에서 `sync: false`로 표시되는 OIDC 값을 모두 입력합니다. 비밀값은 저장소에 커밋하지 않습니다.
4. Blueprint를 적용합니다. DB 생성, Docker 이미지 빌드, 마이그레이션, API 시작 순서로 진행됩니다.
5. Web Service의 Events와 Logs에서 `applied:` 출력과 `/health` 성공을 확인합니다.

필수 OIDC 값은 다음과 같습니다.

| 변수 | 용도 |
|---|---|
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` | API access token 검증 |
| `WEB_OIDC_AUTHORIZATION_URL`, `WEB_OIDC_TOKEN_URL`, `WEB_OIDC_USERINFO_URL` | 웹 로그인 흐름 |
| `WEB_OIDC_CLIENT_ID`, `WEB_OIDC_CLIENT_SECRET` | confidential web client |

OIDC 공급자에는 `https://solo-leveling-staging.soogyung.com/v1/auth/callback`을 허용 callback으로 등록합니다.

## 도메인 연결

1. Render Web Service의 **Settings > Custom Domains**에서 도메인이 등록됐는지 확인합니다.
2. Render가 화면에 표시하는 CNAME 대상을 복사합니다.
3. Hostinger DNS Zone에서 `solo-leveling-staging` 호스트의 CNAME을 그 대상으로 지정합니다.
4. 기존에 같은 호스트로 만든 A/AAAA/CNAME 레코드가 있다면 충돌하지 않도록 하나만 유지합니다.
5. Render가 도메인을 검증하고 TLS 인증서를 발급할 때까지 기다립니다.

## 배포 후 확인

1. `https://solo-leveling-staging.soogyung.com/health`가 `status: ok`를 반환하는지 확인합니다.
2. 루트 주소가 External site를 표시하는지 확인합니다.
3. 실제 테스트 계정으로 로그인하고 callback 이후 논문 목록이 표시되는지 확인합니다.
4. 테스트 PDF를 로컬 MCP에서 동기화한 뒤 웹 상세·메모·이어하기 흐름을 확인합니다.
5. Render Postgres의 복구 수단을 준비한 뒤 마이그레이션 재배포를 시험합니다.

무료 Render Postgres는 장기 운영용이 아니며 생성 후 만료 제한이 있습니다. 이 구성은 종단 간 검증용
스테이징에만 사용하고, 만료 전에 백업하거나 유료 DB로 전환해야 합니다. 실제 사용자 데이터나 비공개
논문 원문은 테스트 환경에 넣지 않습니다.
