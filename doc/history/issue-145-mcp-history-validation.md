# #145 퀴즈 이력 MCP 회귀 기준

2026-11-03 · 최신 main `ea84aa9` 재검증 · macOS 26.7.1 / Python 3.12.14

A의 SQLite 페이지 조회 최적화 전후에 공개 MCP 계약이 유지되는지 확인하는
`tests/unit/mcp/test_quiz_history_pagination.py`를 추가했다. 제품·저장소 코드는 변경하지 않았다.
임시 SQLite와 실제 MCP 메모리 전송을 사용하며 사용자 DB·외부 API·모델은 사용하지 않는다.

## 추가 사례

- 같은 시각의 시도는 ID 사전순이 아니라 나중에 저장한 순서로 반환한다.
- 2개씩 조회하는 페이지 경계에서 동률 시도의 누락·중복이 없다.
- 페이지 사이 같은 시각의 새 제출이 들어와도 이전 커서의 뒤쪽 이력은 유지된다.
  새 조회에서는 새 제출이 첫 항목이며 단계별 최근 시도와 일치한다.
- 저장 순서와 제출 시각이 다르면 제출 시각 내림차순이 우선한다.
- 기본 20개·최대 100개, 101개 이력의 마지막 1개, 마지막 커서 이후 빈 목록을 확인한다.
- 단계 필터·빈 이력·다른 context 격리를 확인한다.
- 없는 커서·다른 단계 및 context의 커서는 INVALID_ARGUMENT이며 커서·근거 본문을 오류에 넣지 않는다.
  해당 오류와 후속 조회 전후 SQLite 파일이 동일하다.
- limit 0/-1/101은 거부한다.
- 성공 응답의 JSON 텍스트와 structuredContent가 같고, 목록 항목은 공개 요약 필드만 포함한다.

## 실행

```sh
PYTHONPATH=src python -m pytest tests/unit/mcp/test_quiz_history_pagination.py tests/unit/mcp/test_quiz_tools.py tests/unit/mcp/test_quiz_contracts.py tests/integration/test_mcp_stdio.py -q
```

결과: **52 passed**. 새 테스트는 파라미터 사례 포함 9개이며 기존 관련 테스트 43개도 통과했다.
실제 Desktop·새 설치 패키지 검증은 아니다.

## 남은 A/B/C 범위

현재 구현은 전체 시도·답안을 읽은 뒤 MCP 어댑터에서 분할한다. 이번 테스트 통과는
공개 계약의 기준선이며 SQL LIMIT 최적화가 완료됐다는 뜻이 아니다.
목록 응답에 답안이 없다는 검증과 DB에서 답안을 로드하지 않는다는 검증을 구분한다.

- A: context/step SQL 조건, LIMIT+1, 저장 순서를 보존하는 커서 조회, 인덱스와 조회량 검증.
- B/C: A 구현에 맞는 서비스·어댑터 연결 후 같은 회귀 테스트 재실행.
- 성능 검증: 큰 이력에서도 해당 페이지 요약만 읽고 답안·정답·근거 본문을 로드하지 않는지 확인.

점수 집계 최적화와 전체 조회 스냅샷 고정은 이번 범위가 아니다. #145는 아직 완료하지 않는다.
