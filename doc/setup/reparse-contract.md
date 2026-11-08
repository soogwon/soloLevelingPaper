# 기존 논문 재파싱 저장 계약 (#116)

## 현재 구현 범위

A 저장 계층은 기존 `paper_versions.version_id`를 유지한 채 새 파싱 결과를 추가한다.
기존 파싱 리비전·청크·번역·색인·학습 context·근거는 수정하거나 삭제하지 않는다.

1. 호출자는 현재 기본 색인의 `parse_revision_id`와 `embedding_set_id`, 명시적 요청 키와
   입력 해시를 `prepare_reparse`에 전달한다.
2. 저장소는 같은 요청을 재생하고, 입력이 달라진 요청 키와 기준 색인 변경을 고정 상태로
   구분한다. 버전별 활성 재파싱은 하나만 허용한다.
3. `save_reparse_result`는 새 파싱 리비전·청크·섹션을 한 트랜잭션으로 저장한다.
4. B는 새 리비전에 새 번역 리비전과 임베딩을 생성하고 벡터 저장을 검증한다.
5. `publish_reparsed_index`는 요청 당시 기본 색인이 그대로인 경우에만 새 색인을 기본으로
   전환한다. 이전 색인은 비기본 상태로 남아 기존 context와 evidence 조회를 계속 지원한다.
6. 실패하면 `fail_reparse`가 작업과 잠금만 정리하며 기존 기본 색인은 유지한다.

## 상태 계약

`ACCEPTED`, `IN_PROGRESS`, `COMPLETED`, `INTERRUPTED`, `FAILED`, `BASE_CHANGED`,
`REQUEST_CONFLICT`를 사용한다. 호출자는 예외 본문을 상태로 파싱하지 않는다.

## 후속 경계

- B: 새 파싱 리비전만 입력으로 번역·임베딩하고, 벡터 수·차원·청크 ID를 검증한 뒤 게시 함수를 호출한다.
- C: 사용자 확인 전에는 재파싱을 접수하지 않는다. 재번역·임베딩 비용과 기존 context 유지,
  새 context 전환 필요성을 안내하는 MCP 도구·카드를 별도로 연결한다.
- 동일 PDF 재등록·질문·상태 조회는 이 계약을 자동 호출하지 않는다.
- 실제 문제 PDF 검증과 Desktop context 전환 확인은 B/C 실행 경로가 연결된 뒤 수행한다.
