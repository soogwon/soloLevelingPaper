# 원격 메모 생성 원장

이 문서는 #502의 A 서버 저장 계약을 설명한다. 원격 메모 생성은 기기 동기화용
`server_annotations`의 `source_instance_id/local_annotation_id`와 분리한다.

## 범위와 원자성

멱등 범위는 인증 계층이 제공한
`(owner_user_id, service_id, context_id, request_key)`이다. `owner_user_id`와
`service_id`를 모델 입력이나 카드 payload에서 받지 않는다.

`PostgresRemoteAnnotationStore.create()`는 다음 작업을 한 PostgreSQL transaction에서 처리한다.

1. owner에 속하고 삭제되지 않은 context와 게시 revision을 잠근다.
2. 모든 선택 구간이 context의 parse/translation revision에 속하고, 문자 위치의 원문이
   `quote_snapshot`과 정확히 같은지 검사한다.
3. `server_context_annotations`에 기록을 저장한다.
4. `server_annotation_create_requests`에 입력 SHA-256과 annotation ID를 저장한다.

검증 실패나 DB 오류가 나면 두 테이블 모두 rollback되므로 request key만 소비되지 않는다.
동일 범위 요청은 transaction advisory lock으로 직렬화한다. 생성·재조회·메모 삭제는
논문 부모 행을 먼저 잠근 뒤 원장/메모를 변경한다. 물리 삭제와도 부모 우선 순서를 맞춰
원장과 메모 사이 교차 잠금을 막는다. 같은 논문의 메모 쓰기는 이 부모 잠금 아래 직렬화된다.

## 입력 지문 v1

`fingerprint_version=1`은 `kind/body/step/segments/creation_origin`을 canonical JSON으로 만든 뒤
SHA-256을 계산한다. 객체 키만 정렬하며 다음 값은 변경하지 않는다.

- 본문과 인용문의 공백 및 Unicode 표현
- segment 배열 순서
- `target`, chunk ID, 문자 구간
- translation revision 및 `creation_origin`

생략 기본값은 `RemoteAnnotationInput`이 명시 값으로 만든 뒤 지문에 포함한다. B가 입력 동일성
계약을 변경하면 기존 지문의 의미를 바꾸지 말고 새 fingerprint version을 추가한다.

## 재생과 삭제

- 같은 키와 같은 지문은 저장된 기록의 현재 body/revision을 `reused=true`로 반환한다.
- 같은 키와 다른 지문은 `annotation_request_conflict`로 거부한다.
- 삭제 시 본문과 구간을 제거하고 요청 원장을 `deleted`로 바꾼다. 같은 키로 재생성할 수 없다.
- 논문 물리 삭제 worker는 annotation과 활성 요청 원장을 context보다 먼저 제거한다. 계정 삭제
  30일 계약 이후 원문 owner/body/quote를 유지하지 않는다.

논문 tombstone을 이용한 재생성 방지는 기존 60일 정책을 따른다. request별 익명 tombstone을 별도
보관하는 기능은 이번 범위에 포함하지 않는다.

## 후속 범위

이번 저장소는 transport와 독립적이다. 다음 PR에서 인증 adapter에 연결된 create/exact lookup,
고유한 미확정 요청 recovery, idempotent confirm 경계를 추가해야 한다. 그 전에는 C 제품 카드의
원격 sender에 연결하지 않는다. 실제 PostgreSQL 통합 테스트는 `SOLO_TEST_POSTGRES_DSN`이 있는
격리 DB에서 실행한다.
