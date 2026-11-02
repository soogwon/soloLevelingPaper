# #52 읽기 전 질문 MCP 연결

## 범위와 계약

기존 ReflectionService와 reflection_answers를 사용한다. DB 스키마·템플릿 ID·질문 문구는
변경하지 않는다. 읽기 후 질문·전후 비교(#53), 템플릿 의미 변경·버전 이관, MCP App 화면은
이번 범위에 포함하지 않는다. 실제 API는 호출하지 않는다.

| 도구 | 입력 | 응답 |
|---|---|---|
| get_reading_questions | context_id, phase=before, 선택적 step | 질문·저장 답변·작성 상태 |
| save_reading_answer | context_id, template_id, text, phase=before, status=draft | 저장 후 읽기 전 질문 전체·작성 상태 |

- phase는 before만 허용한다. after와 null은 거부한다.
- step은 preview/structure/deep_read/review 또는 null이다. 생략·null은 전체 질문이다.
  현재 질문은 preview에만 있으며 다른 정상 단계는 빈 목록이다.
- context_id는 공백만 있으면 거부하며, 없는 context는 NOT_FOUND다. ID를 임의로 정규화하지 않는다.
- text는 문자열 최대 5000자이며 공백·줄바꿈·Unicode를 그대로 저장한다.
  빈 draft는 허용하고 공백만 있는 completed는 INVALID_ARGUMENT다.
- 같은 context·phase·template_id의 답변은 덮어쓴다. 최초 작성 시각을 보존하고 수정 시각을 갱신한다.
  동시 수정 충돌 검사·수정 이력·요청 키는 없다. 응답 유실 시 먼저 조회하고 자동 재저장하지 않는다.
- 저장과 후속 상태 조회는 기존 서비스의 별도 DB 연결을 사용한다. 조회 실패가 앞선 저장의
  롤백을 의미하지 않는다. 다시 조회해 저장 여부를 확인한다.
- 두 도구의 최상위 미지원 필드는 거부한다. expected_revision 같은 미지원 충돌 검사 인자를
  조용히 무시하지 않으며, 충돌 검사 기능 자체를 추가한 것은 아니다.
- 입력 스키마 위반·서비스 입력 오류는 INVALID_ARGUMENT, 없는 context는 NOT_FOUND, 예기치 않은 실패는
  INTERNAL_ERROR로 반환한다. 원래 예외·답변 본문·DB 경로는 서비스 오류 응답에 싣지 않는다.
  두 도구는 SDK의 원본 검증 오류 직렬화 전에 공개 입력 스키마로 검증한다.
  잘못된 타입·길이 초과·필수 필드 누락·미지원 필드는 본문 없이 거부하고 서비스를 호출하지 않는다.
  다른 MCP 도구의 기존 입력·오류 처리에는 적용하지 않는다.

## 응답

필수 키: context_id, phase, items, total, written, completed, status.

items 항목은 template과 answer다. template은 template_id/phase/category/prompt/step을 담는다.
answer는 미작성 시 null, 저장 시 context_id/phase/template_id/text/status/created_at/updated_at이다.
응답에 포함된 사용자 답변은 지시문이 아니라 데이터다.

- total: 이번 응답의 질문 수
- written: 초안을 포함해 저장된 답변 수
- completed: 작성 완료로 표시한 답변 수
- status: not_started / in_progress / completed

조회에 step을 지정하면 집계도 해당 범위만 계산한다. 저장 응답은 전체 범위다.
개별 답변 상태는 draft/completed이며, 전체 completed도 정답 판정이나 학습 단계 완료가 아니다.
기존 학습 진행·요약·퀴즈·근거를 변경하지 않는다.

## 자동 테스트

```powershell
python -X utf8 -m pytest tests/unit/mcp/test_reflection.py tests/unit/test_reflection.py tests/unit/test_reflection_service.py tests/unit/mcp/test_runtime.py tests/integration/test_mcp_stdio.py -q
```

임시 SQLite와 대역만 사용한다. 사용자 DB·번역·생성 API·모델 실행은 필요하지 않다.

## Claude Desktop 수동 확인

수정한 코드의 wheel 설치와 MCP 재시작 후 도구 목록에 두 새 도구가 있는지 확인한다.
기존 설치본에서는 코드 변경이 자동 반영되지 않는다. 이 문서 작성만으로 설치·실사용 검증을
완료한 것은 아니다. 답변 저장은 실제 쓰기이므로 테스트용 context를 우선 사용한다.

1. get_reading_questions를 `{"context_id":"<테스트 context>","phase":"before"}`로 호출한다.
   세 질문과 기존 답변을 확인한다. 기존 답변이 있다면 수정 여부를 먼저 확인한다.
2. 사용자 작성 문구로 save_reading_answer를 한 번 호출한다.

   ```json
   {"context_id":"<테스트 context>","template_id":"before.background","text":"행렬곱의 기본 개념을 알고 있습니다.","status":"draft"}
   ```

3. 같은 context로 조회해 본문과 draft, written 증가를 확인한다.
4. 사용자가 수정 요청한 문구와 completed로 같은 항목을 저장한다. 답변 행이 늘지 않고
   본문·상태만 바뀌며, 다른 두 항목은 보존되는지 확인한다.
5. MCP 재시작 후 같은 context로 다시 조회해 답변 보존을 확인한다.
6. 다른 테스트 context에서는 답변이 공유되지 않는지 확인한다.
7. after·없는 template_id·공백 completed를 요청하면 오류가 나고 기존 답변은 보존되는지 확인한다.

읽기 후 비교 기준과 템플릿 대응 관계는 #53 협의 대상으로 유지한다.

## 검증 결과 (2026-10-31)

- 자동 회귀 테스트: MCP 단위 테스트·기존 reflection 테스트·stdio 통합 테스트에서
  292개 통과, 2개 건너뜀. 설치된 wheel 통합 테스트는 별도로 1개 통과했다.
- 수정 코드로 wheel을 빌드하고 Desktop용 가상환경에 재설치했다.
  `pip check`에서 의존성 오류가 없음을 확인했다.
- Claude Desktop에서 별도 테스트 context를 생성하고 두 새 도구를 확인했다.
  최초 조회는 질문 3개, 답변 모두 null, written=0, completed=0, not_started였다.
- 합성 답변 하나를 draft로 저장하고 재조회했다. 본문이 유지되고
  written=1, completed=0, in_progress로 반환됐다.
- 같은 답변을 수정해 completed로 저장하고 재조회했다. 수정 본문과 상태가 유지되고,
  created_at은 보존되며 updated_at은 갱신됐다. written=1, completed=1,
  in_progress였으며 나머지 두 답변은 null로 유지됐다.
- 두 저장 호출 모두 저장 응답과 후속 조회 결과가 일치했다.

Desktop에서 재시작 후 보존·다른 context 격리·잘못된 입력 거부는 이번 수동 확인에
포함하지 않았다. 실제 번역·생성 API나 ask_paper는 호출하지 않았다.
