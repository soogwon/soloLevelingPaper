# 원격 저장 근거 재조회 검증

관련: #590. 첫 단계는 저장 근거의 읽기 전용 재조회다. 티켓 전체 완료가 아니다.

## 계약

`get_remote_evidence(context_id, evidence_id)`는 인증된 `papers:read` 주체에서 owner를
해석하고 완료된 답변의 저장 근거만 반환한다. owner를 모델 인자로 받지 않는다.
context가 고정한 publication·원문 판본·parse/translation revision·embedding set을
사용하며 active pointer 변경으로 새 결과를 선택하지 않는다. 저장 답변의 SHA-256과
인용 ID·청크·인용문을 대조한다. 재검색·AI 생성·새 질문 접수 포트는 없다.

원문 전체를 반환하지 않고 저장된 원문/번역 인용, 고정 청크 원문의 SHA-256,
PDF 페이지와 별도 인쇄 페이지 표기, section ID를 반환한다. 다른 판본의 페이지가
다를 수 있음을 명시한다. 없는 기록·다른 owner/context·삭제된 논문은 같은
`not_found`, 무결성/DB 장애는 원문 오류를 숨긴 `unavailable`이다.

## 로컬 검증

- 실제 격리 PostgreSQL: 동일 근거 재조회, 타 계정/다른 context/없는 ID 거부,
  active pointer 해제 후 고정 게시 재조회, 논문 삭제 후 차단.
- 완료 답변 payload 또는 저장 인용 변조 시 결과 반환 거부.
- 실제 ASGI MCP 인증/권한 연결과 인증 주체 연결 철회 후 조회 차단.
- FastMCP 실제 구조화 응답·읽기 전용 metadata, DB 오류 내용 은닉.
- 외부 AI·staging 호출 없음. 합성 로컬 DB만 사용한다.

## staging 적용 전 승인할 범위

기존 MCP 전용 DB 역할에 다음 **SELECT만** 추가해야 한다.
`server_processing_evidences`, `server_processing_chunks`, `server_translation_chunks`.
기존 context/publication/execution 등 조회 권한은 유지한다. 신규 권한은 이 PR에서
실제 staging에 적용하지 않는다. 새 웹 이미지 배포와 기존 합성 evidence ID 조회를
승인받은 뒤 실행하며 실패 시 해당 SELECT를 회수하고 이전 웹 커밋으로 복원한다.
DB migration·쓰기 권한·새 자격·worker 설정·PDF/AI 호출은 필요 없다.

## 남은 #590 범위

- 논문 상세·저장 요약·섹션 카탈로그·키워드/중요 문장 조회: 원격 게시 결과의 해당
  저장 계약이 부족하다. 로컬 동기화 `server_summaries`를 원격 고정 게시의 요약으로
  간주하지 않는다. #581 원격 요약과 출처 연결을 확인하고 명시적 빈 상태를 제공한다.
- 근거별 메모 커서 조회·상세 카드 연결 및 별도 annotation scope 검사.
- 실제 Claude 재로그인/다기기/두 계정 종단 검증.
