# 복구·재파싱 웹 검토와 접수

Refs #594. 미리보기 다음 단계이며 실제 실행·새 색인 선택 완료를 의미하지 않는다.

## 제품 흐름

MCP `preview_remote_reprocessing`의 `review_web_path` → 인증 웹에서 읽기 전용
미리보기 → 사용자가 검토 저장 → 결과 저장과 AI 전송을 각각 확인·동의 → 작업 접수.
요청 키는 첫 페이지 주소의 `key`에 고정한다. 재접속/응답 유실 시 같은 주소로 조회하며
자동 POST나 새 키 재접수는 하지 않는다. 로그아웃·인증 만료·화면 이동 후 늦은 응답을 버린다.

비용 계약은 서버 설정의 provider/model·전체 작업 호출 수·전체 입력/출력 토큰 상한,
단가 기준 USD 상한·정책 버전·예산 창·검토 만료로 고정한다. 상한은 실측 비용이 아니다.
재파싱과 번역 복구는 서로 다른 operation과 입력 해시로 동의를 분리한다.
정확한 전송 payload 및 실제 단가 검증은 worker 연결 전 후속 작업이다.

## 접수·실행 경계

- 새 `server_reprocessing_reviews` 원장에 job_id를 원자 생성한다.
- 기존 `full_processing` 큐에는 넣지 않는다. 기존 worker가 잘못 소비할 수 없다.
- 화면과 API는 `execution_enabled=false`, `접수됨·실행 연결 대기`를 명시한다.
- 실행 대기는 자동으로 활성화하지 않는다. 후속 worker는 실제 전송 본문·단가·
  저장/전송 동의와 실행 세대를 재검사하고, 기존 검토 범위를 넘으면 새 동의를 요구해야 한다.
- 사용자/서비스의 공통 토큰 예산을 같은 트랜잭션에서 한 번만 예약한다.
  최대 호출 수는 전체 토큰 상한을 늘리지 않는다.
- 같은 키의 동시 제출/재조회는 같은 job_id다. 다른 context/operation으로 키를 재사용하면 거부한다.
- 접수 전 PDF DB 상태와 해시, 고정 context와 현재 active publication, 동의·만료·
  운영 접수 차단·정책·예산을 재검사한다. R2 실물은 여기서 읽지 않는다.
- 취소 또는 동의 철회 시 접수 상태를 cancelled로 바꾸고 미사용 예약을 한 번 반환한다.
  취소 후 같은 키 제출은 취소 결과만 반환한다.
- 만료된 검토는 접수할 수 없다. 이미 대기 중인 작업의 자동 만료 정리 worker는 후속이다.
  이번 웹에서는 사용자가 취소·예약 반환할 수 있다.
- 기존 publication·active pointer·학습 맥락·과거 근거는 변경하지 않는다.

## 배포 준비 (이번에는 적용하지 않음)

기본 비활성. 다음을 모두 준비한 뒤 별도 승인으로 적용한다.

1. migration `0059_reprocessing_reviews.sql`.
2. 전용 비특권 DB 역할: 미리보기/context/PDF 조회, 부모 논문 잠금,
   이 접수 원장·동의 원장·공통 예산의 필요한 쓰기만 허용. 세부 GRANT SQL과
   제한 역할 실DB 검증은 후속이다. 관리자/기존 MCP 조회 DSN을 재사용하지 않는다.
3. `REMOTE_REPROCESSING_DATABASE_URL`: 외부 호스트는 verify-full TLS.
4. `REMOTE_REPROCESSING_ENABLED=true`.
5. `REMOTE_REPROCESSING_SETTINGS_JSON`에 아래처럼 **운영자가 승인한 실제 값**을 설정.
   다음 값은 로컬 합성 테스트 예시이며 실제 모델 가격/호출 승인이 아니다.

```json
{"config":{"policy_version":"recovery-test-v1","window_key":"test","provider":"synthetic","model":"synthetic","max_calls":2,"max_input_tokens":4000,"max_output_tokens":1000,"input_usd_per_million":"1","output_usd_per_million":"2","review_seconds":3600}}
```

해당 정책·예산 창의 사용자/서비스 예산 행은 별도로 준비해야 한다.
설정만으로 예산을 만들거나 worker를 활성화하지 않는다.
실패 복구는 기능 flag 비활성 및 이전 웹 복원. 적용된 schema와 접수 이력은 보존한다.
예약된 작업은 원래 정책/창으로 취소해야 하며 예산 행부터 삭제하지 않는다.

## 필수 검증과 남은 범위

로컬 합성 PostgreSQL에서 동일 키 동시 접수, 복구, 예약 1회, 취소 반환,
타 계정·삭제·만료·철회·정책·PDF·publication 변경·예산 부족·접수 중단을 검증한다.
웹은 조회만으로 쓰기 없음, XSS 이스케이프, 응답 유실 복구, 로그아웃 후 늦은 응답 차단을 검증한다.
웹 세션·CSRF·기본 비활성·전용 DSN/TLS 설정 경계도 확인한다.

남은 연결: #595 공통 목록/상태/취소 투영, 권한 SQL·실제 배포, 실제 전송 payload/
토큰 계산·단가 승인, #587 worker·lease·자동 만료/예약 반환, 원자 게시·새 색인 명시 선택,
Claude/브라우저 실환경 종단 검증. staging 변경·PDF 전송·API 호출은 이번 작업에 포함하지 않는다.

삭제 정리: context FK 연쇄 삭제 전에 미사용 예약을 반환하는 invoker trigger를 둔다.
따라서 실제 적용 시 정리 전용 역할에도 해당 정책 예산의 SELECT/예약 열 UPDATE가 필요하다.
누락된 예산이나 권한 오류는 삭제 트랜잭션을 취소한다. 권한 확대는 이번에 적용하지 않았다.
