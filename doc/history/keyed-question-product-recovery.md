# 요청 키 기반 제품 MCP 복원 검증 (#119)

## 구성

실제 `create_server`, `submit_paper_question`, `get_paper_question_status`,
`AskExecutionService`, `AnswerService`, SQLite 저장소를 사용합니다.
검색·생성은 합성 대역이며 모델·OpenAI API·사용자 DB를 사용하지 않습니다.
자식 프로세스에는 테스트 DB와 필요한 시스템 환경만 전달하며 사용자 설정·키를 상속하지 않습니다.

## 검증 흐름

1. 임시 DB에 합성 논문·번역·게시 색인·context를 정상 준비합니다.
2. 실제 stdio MCP 프로세스에서 요청 키로 질문을 제출합니다.
3. 제품 서비스가 답변·인용을 저장한 뒤 테스트 대역이 오류를 내 최초 응답을 유실시킵니다.
4. 프로세스를 종료하고 새 프로세스를 시작합니다.
5. 원래 context와 키로 조회하여 저장된 공개 답변 JSON과 동일한 답변을 복원합니다.
6. 같은 입력 재제출은 같은 결과를 돌려주고, 같은 키에서 strict_pages를 바꾸면 CONFLICT입니다.
7. 다른 context의 키와 없는 키는 모두 본문 없는 not_found입니다.
8. 세 번째 프로세스에서는 첫 조회에 SQLite 오류를 주입합니다. 본문 없는 INTERNAL_ERROR 뒤
   정상 조회가 복원되며 추가 검색·생성·근거 저장은 없습니다.

조회·오류·재시작 전후 DB 전체 실행 행과 인용 ID 목록이 동일하며,
검색·생성 호출 수는 각각 1회, 실행 기록은 completed 1건, 인용은 2건인지 검사합니다.
MCP JSON 텍스트와 structuredContent의 일치 및 오류 본문 비노출도 확인합니다.

## 실행

```powershell
python -m pytest tests/integration/test_keyed_question_recovery.py tests/unit/mcp/test_keyed_questions.py tests/unit/test_ask_execution_service.py tests/unit/test_ask_executions.py -q
```

## 한계와 C 후속

2026-11-05 Windows / Python 3.12.14, 기준 main `1a256da`에서 위 검사와
runtime·실제 MCP stdio 회귀까지 **156개가 통과**했습니다. 테스트용 서버 프로세스는
검사 종료 시 회수하며 원본 DB·실제 API·Desktop 설정을 사용하지 않습니다.

- 이 테스트의 응답 유실은 **커밋 후 오류 주입**입니다. 실제 네트워크 단절·60초 타임아웃·
  서버 강제 종료 중 실행을 검증한 것은 아닙니다.
- 제품 MCP와 DB는 실제 구현이지만 전체 런타임 조립·임베딩·검색 모델은 대역입니다.
- 호출 전에 키를 디스크에 보관하고 Desktop 재시작 후 고르는 흐름은 C 호출자 범위입니다.
  이번 테스트는 클라이언트가 알고 있는 같은 키를 사용합니다.
- 실제 Desktop에서 제품 도구로 같은 흐름을 검증하고 키 손상·보관 실패 시 자동 재제출이
  없는지 확인해야 합니다. #182의 합성 호출자 검증을 제품 연결 완료로 간주하지 않습니다.
- #119는 위 호스트 검증이 끝날 때까지 열어둡니다. 테스트만으로 지연 자체가 해결됐다고 판단하지 않습니다.
