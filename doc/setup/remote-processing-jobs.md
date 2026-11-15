# 원격 처리 작업 큐 계약

관련: #392. 원격 worker는 PostgreSQL의 `server_processing_jobs`를 영속 큐로 사용한다.

## 접수와 멱등성

작업 요청은 사용자 범위의 `request_key`와 정규화한 입력의 SHA-256 `payload_hash`를 함께 저장한다.
같은 사용자·키·payload는 기존 작업을 반환한다. 같은 키에 다른 payload가 오면 충돌이며 새 작업을
만들지 않는다. 다른 사용자는 같은 키를 독립적으로 쓸 수 있다.

작업은 반드시 같은 owner의 `server_processing_versions`를 참조한다. 존재하지 않는 버전과 다른
사용자 버전은 모두 같은 not-found 결과로 처리한다.

## claim과 실행 세대

worker는 생성 순서대로 한 건을 `FOR UPDATE SKIP LOCKED`로 claim한다. claim할 때마다 새
`lease_token`을 만들고 attempt를 증가시킨다. heartbeat와 성공·실패 저장은 owner, job ID,
현재 token, 만료 전 lease를 모두 만족할 때만 허용한다.

lease가 만료된 실행은 다음 worker가 새 token으로 회수할 수 있다. 이전 worker의 heartbeat,
완료, 실패와 publication 게시 요청은 모두 거부된다. `SKIP LOCKED`만으로 실행권을 보장하지 않는다.

lease 만료 판정과 시각 기록은 모두 DB 시계(`CURRENT_TIMESTAMP`)로 한다. API와 worker 서버의
시계가 어긋나도 claim·heartbeat·완료·게시의 판정이 서로 달라지지 않는다.

lease가 만료됐는데 다시 claim할 수 없는 작업은 다음 claim 때 `failed`로 닫는다. 최대 시도에
도달한 작업은 `max_attempts_exhausted`, 재시도 불가로 접수한 작업은 `lease_expired_not_retryable`을
기록한다. worker가 죽어도 작업이 `running`으로 남지 않는다.

## 상태 전이

```text
queued -> running -> succeeded
                  -> queued      # retryable, attempt < max_attempts
                  -> failed      # 영구 실패 또는 최대 시도 도달
queued/running -> cancelled
expired running -> running       # 새 worker와 새 lease token
expired running -> failed        # 재시도 불가이거나 최대 시도 도달
```

성공·실패·취소 상태는 종료 시각을 기록하고 lease 필드를 비운다. 오류는 본문이나 외부 제공자
응답을 저장하지 않고 제한된 소문자 `error_code`만 저장한다. 모든 작업을 자동 재시도 가능한 것으로
보지 않으며 접수자가 `retryable`과 최대 시도를 명시한다.

## 프로세스 경계

API는 접수와 상태 조회를, worker는 claim과 실행을 담당하는 별도 프로세스를 기본으로 한다.
동일 프로세스 운영은 메모리와 장애 격리 실측 뒤 별도 결정한다. 이 티켓은 worker 실행 이미지나
운영 인스턴스를 만들지 않으며, 실제 호스팅 사양은 #400에서 검증한다.
