# 서버 PostgreSQL 마이그레이션

중앙 API를 시작하기 전에 같은 릴리스의 저장소 루트에서 서버 마이그레이션을 먼저 적용한다. 명령은
`DATABASE_URL`만 읽으며 DSN이나 SQL 본문을 출력하지 않는다. 다른 위치에서 실행할 때는 같은 릴리스의
`migrations/server` 경로를 `--migrations-dir`로 명시한다.

```powershell
$env:DATABASE_URL = "postgresql://..."
solo-leveling-server-migrate --check
solo-leveling-server-migrate
solo-leveling-server-migrate --check
```

`--check`는 미적용 파일이 있으면 목록을 출력하고 종료 코드 2를 반환한다. 적용 명령은 PostgreSQL
transaction advisory lock을 얻은 뒤 `migrations/server`의 파일을 번호순으로 한 트랜잭션에서 실행한다.
성공한 파일의 이름과 줄바꿈을 정규화한 SQL의 SHA-256을 `server_schema_migrations`에 기록한다.

이미 적용된 SQL 파일은 수정하지 않는다. 적용 이력과 현재 파일의 이름 또는 checksum이 다르거나,
코드에 없는 버전이 DB에 기록돼 있으면 명령은 아무 마이그레이션도 적용하지 않고 실패한다.

## 1단계 종단 간 확인 순서

1. 배포 DB 백업 또는 복구 지점을 만든다.
2. 동일 빌드에서 `--check`, 적용, 다시 `--check`를 실행한다.
3. 중앙 API의 `/health`가 정상인지 확인한다.
4. 테스트 계정으로 기기를 연결하고 논문 동기화 확인 카드의 v3 범위를 승인한다.
5. 로컬 outbox가 논문, 학습 기록, 요약 순서로 `synced`가 되는지 확인한다.
6. 웹 세션으로 논문 목록과 상세를 조회하고 선택 버전, 학습 위치, 저장 요약이 같은지 확인한다.
7. PDF와 추출 원문, 근거 본문, 로컬 경로가 서버 DB 및 요청 로그에 없는지 확인한다.

운영 DB 자격이나 실제 사용자 논문으로 최초 검증하지 않는다. 테스트 계정과 공개 테스트 논문을 사용한다.
