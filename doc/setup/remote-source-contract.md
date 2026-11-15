# #390 고정 원문·번역 조회와 기존 검사 연결

`remote_sources.py`는 원격 검색 hit의 ID를 저장 출처와 대조해 기존 SearchResult로 변환한다.
서버 context/owner/publication 연결은 별도 binding으로 유지하고, 검색 점수·순위·페이지·섹션과
원문/번역 문자열을 그대로 보존한다. 다른 owner/revision, 누락·추가·중복 ID, 페이지/섹션 불일치,
일반 후보의 번역 누락은 최신본이나 원문을 번역으로 사용하지 않고 거부한다.

`RemoteSourceReader.read_chunks(binding, ids)`와 `following_chunks(binding, ordinals)`는 **A adapter가
구현해야 할 포트 초안**이다. 한 읽기 snapshot에서 현재 조회 권한·소속·게시 집합·정확한 저장 원문/번역을
확인해야 한다. B DTO를 생성했다는 사실은 DB 소속 증명이 아니다. 실제 DB 구현은 이번 PR에 없다.

인접 보충은 기존 supplement_continuations/completion_excerpt/인용 검사를 그대로 호출한다.
binding·요청 ordinal·중복 ID 검사 후 변환하며 다른 publication/translation을 섞지 않는다.
기존 제한처럼 기본 페이지 범위 밖은 허용된 첫 연결 문장까지만 인용하고 정렬 미확인 quote_ko는
반환하지 않는다. source의 저장 번역을 원문으로 덮어쓰거나 기존 검사를 완화하지 않는다.
현재 번역 검색의 기존 검사는 저장 번역이 없는 후보를 거부한다. 그런 후보를 원문으로 채워 통과시키지 않는다.

strict_pages는 범위 밖 후보가 반환되면 거부하며, 같은 섹션·인접 청크·정상 연결만 보충한다.
재귀 보충은 기존 검사에서 계속 거부한다. 원문/번역 전체를 임의 결합·정규화하지 않는다.

## 미완료 경계

- A의 실제 reader·현재 접근/삭제 상태 검사·게시 manifest ID 집합 검증이 필요하다.
- 원격 근거/답변 저장은 A의 논리 실행·lease와 같은 원자 경계에서 별도로 연결해야 한다.
  이번 코드에는 DB 쓰기나 저장 대역을 넣지 않았고, 기존 local EvidenceWriter를 원격 저장으로 재사용하지 않는다.
- 저장 직전 동일 binding과 실제 DB 인용 일치 재검증, 원자 답변/근거 저장 및 응답 유실 재생이 필요하다.
- composition→이 변환→생성/의미 검증의 제품 연결과 실제 pgvector/SDK 종단 검증은 남았다.

이번 테스트는 source reader 대역과 기존 실제 검사 함수를 사용한다. 실제 DB·파일·외부 API·모델은
사용하지 않는다. 제품 도구도 공개하지 않으며 #390은 열어둔다.
