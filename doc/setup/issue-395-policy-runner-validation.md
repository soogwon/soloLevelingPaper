# 원격 질문 실행 정책·runner 검증

## 범위

- A: 접수 시 서버 정책 snapshot과 DB 시계 deadline을 고정한다. 기존 정책 없는 행은 유지하며 새 runner는 사용하지 않는다. heartbeat는 현재 lease·기한·삭제·동의·전송 제어를 검사한다. 실패·취소·완료는 현재 실행 상태를 조건으로 갱신한다.
- B: 영속 정책이 호출자의 임의 예산보다 우선한다. 새 transport·재claim으로 호출 수를 초기화하지 않는다. provider 읽기 timeout은 단계 상한으로 제한한다. 전체 deadline 이후 저장은 거부한다.
- C: state·terminal_reason·usage_state를 분리하고 기존 결과 조회를 유지한다. 예산 종료와 미확정 사용량을 동시에 안내한다. 조회 실패나 키 유실로 새 실행을 만들지 않는다.

## 검증

실제 로컬 PostgreSQL/pgvector에서 정책 불변, 재claim 후 오래된 worker 차단, deadline 비연장, 동의 철회 후 게시 rollback, 취소/완료 경합, 영속 호출 상한, 늦은 usage 정산 후 failed 유지 검증. HTTP는 합성 대역이며 실제 유료 API 호출은 0회다.

## 남은 범위

- 정책의 운영 수치·동기/비동기 기준은 확정하지 않았다. 테스트 수치를 운영 기본값으로 쓰지 않는다.
- runner는 주입한 실행 함수를 한 번 수행하는 포트다. 제품 접수의 질문 영속 보관, 큐 소비, 검색·생성 factory 조립과 원격 MCP 도구 연결은 별도 작업이다.
- transport의 읽기 timeout은 소켓 무응답 제한이며 전체 처리의 강제 프로세스 종료를 보장하지 않는다. DB deadline 검사는 늦은 결과 게시를 막지만 이미 전송된 요청의 과금·물리 중단을 보장하지 않는다.
- 새 공개 안내 함수의 실제 Claude Desktop 표시와 staging migration/권한 적용은 미검증이다.
- 중간 partial snapshot은 추가하지 않는다. 기존 원자 저장된 partial 결과 재생은 유지한다.
