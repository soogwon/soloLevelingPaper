# 원격 삭제 백업 복원 훈련

이 절차는 #394의 tombstone 재적용 완료 조건을 운영 데이터와 분리해 검증한다.
격리 PostgreSQL과 격리 R2 버킷에는 공개 가능한 합성 데이터만 넣는다.

## 안전 경계

- 운영 DB, 운영 R2 버킷, 실제 사용자 식별자와 실제 PDF를 사용하지 않는다.
- PostgreSQL 복원본에는 애플리케이션과 worker 트래픽을 연결하지 않는다.
- 삭제 journal은 DB 백업과 분리하며 `owner_user_id`, `paper_id`, 삭제 요청 키만 보관한다.
- 토큰, DB URL, R2 key 자격, 원문과 객체 내용을 기록하거나 커밋하지 않는다.
- 훈련이 끝나면 임시 자격, 복원 DB, 원본 훈련 DB와 R2 버킷을 제거한다.

## 고정 합성 대상

| 항목 | 값 |
|---|---|
| owner | `issue-394-restore-drill` |
| paper | `39400000-0000-4000-8000-000000000001` |
| object | `private/issue-394/restore-drill.pdf` |
| request key | `issue-394-delete-request-1` |

고정 ID는 비밀정보가 아니며 복원 전후에 같은 대상을 판정하기 위한 journal 역할을 한다.

## 실행 순서

1. 최신 `main`의 서버 마이그레이션을 빈 격리 PostgreSQL에 적용한다.
2. 합성 논문, 처리 버전, 업로드 행과 최소 PDF 헤더를 가진 R2 객체를 만든다.
3. 삭제 전 복원 시점이 백업 창에 들어온 것을 확인한다.
4. `PostgresRemoteDeletionStore.request`로 tombstone을 설정한다.
5. `create_remote_deletion_cleanup`을 실제 PostgreSQL/R2 adapter와 실행한다.
6. DB 자식과 R2 객체가 사라지고 부모 tombstone과 완료 요청만 남는지 확인한다.
7. 같은 worker를 다시 실행해 `idle`인 멱등 결과를 확인한다.
8. 삭제 전 시각으로 새 PostgreSQL 인스턴스를 복원한다.
9. 복원본을 외부 트래픽에서 격리한 채 합성 행이 다시 나타난 것을 확인한다.
10. 별도 journal의 고정 owner/paper/request를 복원본에 재적용하고 삭제 worker를 실행한다.
11. 복원된 DB 자식과 재생성한 같은 R2 객체가 다시 제거되는지 확인한다.
12. tombstone 뒤 처리 버전과 업로드 재삽입이 `paper_tombstoned`로 거부되는지 확인한다.

## 판정 증거

티켓에는 날짜, 기준 커밋, PostgreSQL/R2의 격리 여부와 다음 boolean·개수만 기록한다.

- 첫 삭제 요청 `complete`, 재실행 `idle`
- tombstone 존재, 제목·저자·연도 제거
- 처리 버전·업로드·vector 행 0
- R2 객체 404
- 복원 직후 합성 행 존재
- journal 재적용 뒤 다시 처리 버전·업로드 0, R2 객체 404
- tombstone 뒤 늦은 쓰기 거부

DB URL, 사용자명, 비밀번호, access key, secret key와 객체 본문은 증거에 포함하지 않는다.

## 2026-11-16 실행 결과

- 기준 커밋: `f7821f5`
- PostgreSQL: Render PostgreSQL 18의 전용 훈련 DB와 삭제 전 시점의 별도 PITR 복원 DB
- 객체 저장소: 전용 Cloudflare R2 비공개 버킷
- 운영 서비스 연결: 없음
- 데이터: 고정 ID를 사용한 합성 논문과 최소 PDF 객체만 사용

첫 삭제에서 요청은 `complete`, 같은 worker의 재실행은 `idle`이었다. 부모 논문의
tombstone은 유지됐고 제목·저자·연도는 제거됐으며 처리 버전·업로드·vector 행은
각각 0, R2 객체 조회는 404였다.

삭제 전 시점으로 복원한 PostgreSQL에서는 합성 논문 1건, 처리 버전 1건, 업로드 1건이
다시 나타났고 tombstone과 삭제 요청은 없었다. R2는 PostgreSQL PITR 범위에 포함되지
않으므로 동일한 전용 버킷에 같은 key의 합성 PDF를 다시 넣어 객체 백업 복원을
재현했다. 별도 journal의 고정 owner·paper·request key를 복원 DB에 적용한 결과 삭제
요청은 다시 `complete`, 재실행은 `idle`이었고 처리 버전·업로드는 0, 객체는 404가
됐다. tombstone 뒤 처리 버전과 업로드 변경은 모두 `paper_tombstoned`로 거부됐다.

따라서 DB 복원으로 삭제 대상이 되살아나더라도 외부 journal을 재적용해 재삭제할 수
있고, tombstone이 이후 처리 쓰기를 막는다는 #394 완료 조건을 격리 환경에서 확인했다.

## 검증 범위와 한계

이번 fixture는 논문·처리 버전·업로드와 최소 PDF 객체로 구성했다. vector 0건은
비어 있는 테이블의 결과이며, 비어 있지 않은 벡터·번역·청크의 복원 후 삭제 실측을
뜻하지 않는다. 해당 자식의 연쇄 삭제는 #474의 PostgreSQL/pgvector 회귀 증거와 구분한다.
R2는 백업 서비스로 복원하지 않고 같은 합성 객체를 다시 넣었다. 실제 R2 백업·복제 정책,
운영 스케줄, 보존 기한 준수 실측, #368의 클라이언트·다중 기기 검증은 별도다.
