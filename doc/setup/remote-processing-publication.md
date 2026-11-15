# 원격 처리 데이터 격리와 게시 계약

관련: #77, #389. 이 문서는 원격 처리의 저장 경계만 정한다. 검색 점수와 번역 품질 정책은
후속 티켓에서 정한다.

## 사용자 격리

`server_processing_versions`부터 파싱·번역·색인·context·근거까지 모든 식별 관계에
`owner_user_id`를 포함한다. 하위 테이블의 외래 키는 owner, paper, version, revision을 함께
참조하므로 애플리케이션이 잘못된 ID 조합을 전달해도 다른 사용자의 객체나 다른 revision을
연결할 수 없다.

저장소 메서드는 owner를 선택 인자가 아닌 첫 필수 인자로 받는다. 찾지 못한 객체와 다른
사용자 소유 객체는 모두 같은 `*_not_found` 오류로 처리한다.

PostgreSQL RLS는 이번 단계에서 켜지 않는다. 서비스 연결 계정의 `BYPASSRLS` 여부와 connection
pool의 transaction-local 사용자 컨텍스트를 함께 검증하지 않은 RLS는 잘못된 안전 신호가 될 수
있다. 후속 스파이크에서 다음을 모두 통과할 때 방어 심층으로 채택한다.

- 서비스 계정이 정책을 우회하지 않는다.
- 매 transaction에서 사용자 컨텍스트를 설정하고 pool 반환 전에 사라진다.
- 두 사용자의 교차 ID 표 테스트가 저장소 제약과 RLS 양쪽에서 같은 결과를 낸다.
- migration·worker처럼 명시적으로 우회가 필요한 역할을 별도 계정과 감사 로그로 제한한다.

이 보류는 복합 FK와 owner 필수 조회를 대체하지 않는다.

## 원자적 게시

처리 중인 revision은 `building` 상태로 적재한다. 파싱, 번역, embedding set이 모두 `ready`이고
다음 manifest 검증을 통과한 경우에만 새 publication을 만들고
`server_processing_versions.active_publication_id`를 같은 transaction에서 교체한다.

- 파싱 chunk 수와 번역 입력 chunk 수가 같다.
- revision에 기록된 수와 실제 파싱·번역 행 수가 같다.
- 번역 완료 수와 확정 누락 수의 합이 전체 chunk 수와 같다.
- 색인된 chunk 수가 번역 완료 수와 같다.
- 누락 항목은 중복되지 않은 `chunk_id`와 비어 있지 않은 `reason`을 갖는다.
- 누락 항목은 이 파싱 revision의 chunk 중 번역 행이 없는 것과 정확히 일치한다. 없는 chunk나
  이미 번역된 chunk를 누락으로 적으면 개수가 맞아도 거부한다.
- 번역된 chunk가 하나도 없는 집합은 `partial`로도 게시하지 않는다.

`complete` publication은 누락을 허용하지 않는다. `partial` publication은 하나 이상의 명시적
누락을 요구한다. 어떤 실패 사유를 partial로 허용할지는 B의 품질 정책이 결정하지만, 허용된
집합이 결정된 뒤에는 그 집합 전체가 manifest에 고정돼야 한다. 따라서 후속 정책 변경이 이
migration이나 publication의 불변 revision 연결을 다시 바꾸지 않는다.

실패·취소된 revision이나 개수가 맞지 않는 집합은 게시되지 않는다. 기존 context와 evidence는
생성 당시 publication을 계속 참조하므로 재처리 뒤에도 자동으로 새 revision으로 이동하지 않는다.
게시 작업은 현재 유효한 job lease token을 함께 제출해야 한다. 저장소는 job을 같은 transaction에서
잠그고 `running` 상태, token 일치, 만료 전임을 확인한다. 임대가 끝난 worker는 새 revision을
적재했더라도 active publication을 바꿀 수 없다.

## 확정 뒤 불변성

`building`을 벗어난 파싱·번역 revision과 embedding set은 다시 수정할 수 없다. chunk와 번역 행은
소속 revision이 `building`일 때만 추가·변경·삭제할 수 있다. DB 트리거가 이를 강제하므로 임대가
끝난 worker가 게시된 내용을 뒤늦게 바꾸지 못한다. 트리거는 부모 revision을 `FOR SHARE`로 읽어
`ready` 전환과 늦은 쓰기가 엇갈리지 않게 한다. 재처리 결과는 항상 새 revision으로 적재한다.

## 삭제 경로

논문(`server_papers`) 또는 처리 버전을 지우면 그 버전의 revision, chunk, 번역, embedding set,
publication, context, 근거, 작업이 함께 지워진다. 참조 중인 revision 하나만 따로 지우는 것은
계속 거부한다. 언제 지우는지와 R2·백업 정리는 #394에서 정한다.

## 실제 PostgreSQL 검증

`tests/integration/test_remote_processing_postgres.py`는 `SOLO_TEST_POSTGRES_DSN`이 있을 때 임시
스키마에 서버 마이그레이션 전체를 적용하고 교차 사용자 연결, 늦은 worker, 누락 목록, 확정 뒤
쓰기, 논문 삭제를 확인한다. CI의 `python` job이 일회용 PostgreSQL 16으로 실행한다.

## 운영 적용 범위

`0014_remote_processing_schema.sql`은 새 테이블만 추가하고, `0016_remote_processing_integrity.sql`은
그 테이블에 삭제 경로 FK와 불변성 트리거를 더한다. 기존 동기화·메모·PDF 테이블을
변경하지 않는다. 운영 DB 적용은 백업·승인 뒤 서버 migration 절차를 따른다. 이번 변경은
운영 DB에 자동 적용하거나 원격 처리를 공개하지 않는다.
