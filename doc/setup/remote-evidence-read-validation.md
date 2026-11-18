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

## 최초 staging 적용 범위 (#602 당시)

2026-11-18 승인 후 기존 MCP 전용 DB 역할에 다음 **SELECT만** 추가했다.
`server_processing_evidences`, `server_processing_chunks`, `server_translation_chunks`.
기존 context/publication/execution 조회 권한을 유지하고 기존 자격의 직접 접속으로
읽기 성공·쓰기 차단을 검증했다. #602와 #603 병합본을 staging에 배포했다.
이번 테스트·문서 PR은 새 권한이나 배포 변경을 포함하지 않는다.

## 남은 #590 범위

- 논문 상세·저장 요약·섹션 카탈로그·키워드/중요 문장 조회: 원격 게시 결과의 해당
  저장 계약이 부족하다. 로컬 동기화 `server_summaries`를 원격 고정 게시의 요약으로
  간주하지 않는다. #581 원격 요약과 출처 연결을 확인하고 명시적 빈 상태를 제공한다.
- 실제 Windows·다기기·두 계정의 #590 종단 검증.
- 실제 카드 실패 시 호스트 텍스트 폴백은 [#609](https://github.com/soogwon/soloLevelingPaper/issues/609)로 분리한다.

## 근거별 메모와 상세 카드

`list_remote_evidence_annotations(context_id,evidence_id,after,limit)`와
`show_remote_evidence_detail(context_id,evidence_id)`는 기존 근거 무결성 검사를 거친 후
`papers:read`와 `annotations:read`를 모두 검사한다. annotation 전용 저장소에서
같은 owner/context와 고정 chunk/parse revision, 번역이면 translation revision도
일치하는 살아 있는 기록만 LIMIT 이전에 필터링한다. 연결 없는 맥락 메모는 제외한다.
이는 **같은 근거 청크**의 기록이며 인용문과 정확히 겹치는 offset만의 목록은 아니다.
불변 annotation ID 커서로 더 보기를 지원하며 새로고침은 첫 페이지부터 한다.
페이지를 넘기는 중 생성·수정·삭제에 대한 시점 고정 스냅샷은 제공하지 않는다.

상세 카드는 원문/번역 인용과 판본·페이지 안내, 메모/하이라이트 및 구간 인용을
표시한다. SDK 구조화 응답을 사용하고 본문은 textContent로만 표시한다. 더 보기의
연결 binding이 바뀌거나 응답이 잘못되면 이전 기록까지 지운다. 새 입력·취소·pagehide
후 이전 비동기 응답은 무시한다. 토큰 갱신 시 새 카드를 열어야 하며 호스트의 실제
로그아웃 이벤트를 카드에 전달한다는 보장은 없다.

로컬 검증: 23개 연결 기록과 23개 미연결 기록을 섞은 커서 완주, 원문/번역 리비전
필터, 삭제/다른 owner 차단, 실제 저장 답변→근거→메모 카드 조립을 검사한다.
UI에서는 더 보기·새로고침·늦은 응답·연결 변경·중복 페이지·HTML 비실행을 검사한다.

이 단계는 DB migration·새 권한을 추가하지 않는다. 기존 product 조회 pool과
annotation pool을 분리해 사용한다. #603 staging 배포본에서 macOS 시크릿·일반
대화 카드, 20+1 페이지 이동·새로고침, 같은 계정의 MCP 연결 해제·재인증 후
동일 근거·메모 재조회가 확인됐다. Claude 계정 자체 재로그인이나 Windows·
다기기·두 계정 종단 검증으로 확대 해석하지 않는다. #590 전체 종료 조건은 미충족이다.

## 카드 리소스 실패와 텍스트 폴백 (2026-11-18)

실호스트 실패 주입과 표시 검증은 [#609](https://github.com/soogwon/soloLevelingPaper/issues/609)에서 추적한다.
제품 결함이 확정된 것은 아니며 실패 재현·검증 공백을 분리한 티켓이다.

`show_remote_evidence_detail`은 HTML 리소스 조회와 별도로 FastMCP의 텍스트
content 및 structuredContent를 반환한다. HTML을 열지 못하면 텍스트 응답의
`result.evidence`에서 원문·번역·판본·페이지를, `annotation_page`에서 메모와
`next_cursor`를 확인한다. 다음 페이지는 같은 context/evidence와 반환된 커서를
`list_remote_evidence_annotations(after=next_cursor)`에 전달한다. 카드 실패를
새 질문 생성·쓰기·AI 호출의 재시도 이유로 사용하지 않는다.

회귀 테스트 `test_evidence_text_survives_card_resource_failure`는 실제 FastMCP
등록·직렬화 경로에서 카드 파일 누락과 읽기 권한 오류를 주입한다. 리소스 읽기가
실패해도 텍스트 JSON이 구조화 결과와 같고, 인용·메모·커서를 유지하며 다음
페이지를 조회할 수 있는지 검사한다. 인증 scope를 회수하면 텍스트도
`not_authorized`만 반환하는지 확인한다. 관련 도구 테스트 16개 통과.

macOS Claude Desktop 5.11492.5의 일반 합성 대화에서도 텍스트 인용·메모·커서가
제공됐다. 그러나 DevTools의 카드 URL/도메인 차단 규칙에도 실제 카드 HTML은
200으로 로드되고 카드가 표시됐다. 일부 규칙의 affected 카운트만으로 카드
실패를 입증할 수 없으므로 **실호스트 카드 로드 실패 시 자동 폴백은 미검증**이다.
초기 입력 규칙 적용 시도 역시 실패 재현으로 세지 않는다. 모든 임시 규칙을
제거하고 요청 차단을 끈 뒤 개발자 모드를 해제·재시작하여 원복했다.

실제 HTML 실패를 확실하게 주입할 수 있는 격리 호스트/테스트 MCP에서의 사용자
표시 검증, Windows·다기기·두 계정 종단 검증 및 논문 전체 상세 계약은 남아 있다.
추가 OpenAI/번역/임베딩 호출·PDF 전송·staging 권한/데이터 변경은 없었다.
