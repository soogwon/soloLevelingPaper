# #517 실행별 권한·영속 사용량 연결

## A: 서버 경계

`PostgresProcessingAuthorization`은 기존 검증된 R2 입력 조회를 상속하고 단계마다
owner/job/원래 lease/paper/version/크기/SHA-256/현재 동의/삭제 상태를 확인한다.
같은 owner/job의 reserved·unknown 또는 실제 사용량이 예약을 초과한 원장이 있으면
파싱·번역·임베딩·결과 반환을 허용하지 않는다. 이전 lease의 미정산 사용도 유지한다.
동의 철회나 실행 실패가 이미 전송된 사용량을 취소·환급하지 않는다.

`dispatch_intent()`는 DB의 접수 동의와 입력 해시를 사용한다. 사용자에게 받은 임의 동의나
다른 job의 intent를 사용하지 않는다. `ledger()`는 기존 PostgresDispatchLedger에 동일 작업
scope를 연결한다. 논문을 먼저 잠그고 실행·입력·동의 행을 같은 예약 트랜잭션에서 재검사한다.
서비스/사용자 예산, 운영 중지, 중복 전송, 사용량 정산은 기존 원장 계약을 사용한다.
새 예산 수치나 운영 설정은 만들지 않는다.

## B: 실행별 조립

RemoteProcessingWorker의 `stages_factory(source, check_allowed)` 포트는 R2 검증 후 호출된다.
factory는 매 작업마다 새 단계·번역 transport·임베더를 만들고 check_allowed를 각 단계와
임베더에 연결해야 한다. 이 callback은 정확한 source와 현재 입력 권한, heartbeat 실패를
함께 검사한다. 기존 stages 인자는 호환성을 위해 남지만 실제 조립은 factory 경로를 사용한다.
shared callback을 변경하거나 전역 모델/전송 상태를 덮어쓰지 않는다.

권한 검사는 협력적이다. 이미 실행 중인 모델/HTTP를 강제 종료하지 않으며 최종 저장·게시 CAS는
별도 필수다. 원장 authorize 직전 worker callback은 DB 연결을 빌리기 전에 실행한다.
DB 내부 scope 검사는 동일 connection을 써 중첩 pool 대기를 피한다.

## C: 공개 상태와 남은 범위

prepared 반환은 성공·검색 가능·게시 완료가 아니다. heartbeat 오류가 usage unknown을
가려도 실제 원장을 별도 조회해야 하며 공개 실행 상태를 비용 상태로 덮어쓰지 않는다.
본 PR에는 HTTP 상태 확장, 실제 provider 조립, 모델 이미지, staging 저장·게시가 없다.
#517과 #396은 유지한다. 실제 배포·API 호출은 승인 후 별도로 검증한다.

## 검증

단위: source 결합, 미정산 검사, DB 연결 전 callback 거부, 입력 저장소가 허용하더라도
heartbeat 실패가 다음 단계 callback에 전달되는지 검사한다.
격리 PostgreSQL: reserved/unknown/overrun 보존, 늦은 정산 뒤 다음 전송, 같은 키 재사용 거부,
intent의 7개 필드 변조, 취소·삭제·동의 철회·lease 만료·운영 중지 이후 예약 0건을 검사한다.
로컬 DSN이 없으면 DB 검사는 미실행이며 CI의 실제 PostgreSQL 통과를 병합 조건으로 사용한다.
