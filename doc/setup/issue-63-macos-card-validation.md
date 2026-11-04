# #63 macOS 카드 검증

## 대상과 환경

- 검증일: 2026-11-03
- macOS 26.7.1, Apple Silicon, Python 3.12.14, Claude Desktop
- 읽기 전 카드: PR #139, `f0199b600b6cc0b3967c9e41cd25b53764529402`
- 퀴즈 카드: PR #144, `2c634abe041616e5ba33122abb00fef50f2e517c`
- 두 PR의 소스를 별도 경로에서 실행했다. PR 통합 결과나 새 wheel 설치를 검증한 것은 아니다.
- 기존 개발 Python과 각 소스의 `PYTHONPATH`를 사용했다.
- 임시 DATA_ROOT 두 개에 합성 데이터만 저장했다. 실제 논문, 사용자 학습 이력, 실제 API 키는 사용하지 않았다.
- 로컬 모드와 외부 API 차단을 설정했다. Claude 호스트에는 합성 데이터만 표시했다.

## 자동 검증

| 대상 | 범위 | 결과 |
| --- | --- | --- |
| PR #144 | `test_quiz_contracts.py`, `test_quiz_tools.py`, `test_mcp_stdio.py` | 43 통과 |
| PR #139 | `test_reflection.py`(단위 및 MCP), `test_reflection_service.py`, `test_mcp_stdio.py` | 56 통과 |
| 퀴즈 UI | `node --test quiz.test.mjs` | 3 통과 |
| 읽기 전 UI | `node --test reading.test.mjs` | 3 통과 |
| 두 UI 빌드 | 각 디렉터리의 `node build.mjs` | 성공, 생성 HTML이 커밋된 파일과 바이트 단위로 일치 |

Python 집계에는 공통 stdio 테스트가 중복되므로 99개의 서로 다른 테스트로 해석하지 않는다.
UI 자동 테스트는 응답 실패 후 동일 키 재시도, 늦게 도착한 이전 응답 배제,
퀴즈 변경 시 자동 재전송 방지, 저장 결과가 불확실한 경우 재조회,
미저장 입력 보존 등의 회귀 시나리오를 포함한다.

## Claude Desktop 실제 조작

### 퀴즈

1. `get_quiz(context_id="ctx", step="preview")`로 카드를 표시했다.
2. 객관식 두 번째 선택지와 단답형 `1`, `2`를 직접 입력하고 제출했다.
3. 카드에 `채점 결과: 1 / 3`이 표시됐다.
4. 오답 원문 근거 버튼으로 합성 근거의 PDF 페이지와 `First.`, `Second.`를 확인했다.
5. SQLite를 읽기 전용으로 확인했다. 시도 1건, 답안 3건, 제출 요청 1건이며 정답 수는 1/3이었다.

### 읽기 전 질문

1. `get_reading_questions(context_id="ctx", phase="before")`로 3개 질문을 표시했다.
2. 첫 질문에 `Mac 합성 답변입니다.`를 입력하고 초안 저장했다. 작성 1개, 완료 0개가 표시됐다.
3. 작성 완료를 눌렀다. 작성 1개, 완료 1개로 바뀌었다.
4. 저장 상태 다시 조회 후에도 내용과 완료 상태가 유지됐다.
5. SQLite의 `before.background` 한 행이 같은 본문과 `completed` 상태로 저장됐음을 확인했다.

Claude의 대화 본문은 카드가 없다는 설명과 최초 조회 상태를 출력했지만,
실제 내장 카드가 표시됐고 직접 조작 및 DB 저장이 성공했다.
대화 본문을 카드의 현재 상태에 대한 증거로 사용하면 안 된다.

## 서버를 새로 실행한 뒤 재조회

Claude에서 저장한 합성 DB를 유지하고 각 PR의 서버를 새 자식 프로세스로 실행했다.
실제 stdio MCP 연결을 통해 다음 읽기 전용 호출을 검증했다.

- `list_quiz_attempts`: preview 시도 1건 조회
- `get_quiz_attempt`: 저장된 문항별 결과 3개, 점수 1/3 조회
- `get_quiz_scores`: preview 시도 1건, 최근·최고 점수 모두 1/3; 다른 단계는 미응시
- `review_quiz_attempt`: 오답 2개, 각각 근거 1개와 2개 조회
- `get_reading_questions`: 한국어 답변과 completed 상태 유지, 전체 3·작성 1·완료 1
- 조회 전후 시도·답안·요청 수가 1·3·1로 동일하여 중복 제출이 없음을 확인

이 추가 검증은 실제 MCP 프로세스 재실행 검증이며, 재실행 후 Claude 화면을 다시 연 검증은 아니다.
재현 스크립트는 `/tmp/solo63-macos-20261103/check-reopen.py`에 보관했다.

## 판정과 남은 범위

이번 범위에서 제품 코드 수정이 필요한 실패는 발견하지 못했다.
다만 #63 전체를 종료할 근거는 아직 부족하다.

- 읽기 후 질문 및 읽기 전후 비교 흐름과 #53 의존 범위는 이번 검증에서 제외했다.
- 두 PR 병합 후 통합 버전과 설치 패키지는 별도 검증해야 한다.
- Windows/Linux 실제 호스트 동작은 검증하지 않았다.
- 실제 UI에서 통신 실패를 강제하지 않았으므로 실패 복구 판단은 자동 테스트 범위에 한정된다.
- 기존 `solo-leveling-dashboard-test` 연결의 시작 실패 경고가 있었으나, 이번 두 카드 연결은 정상 동작했다. 기존 연결은 변경하지 않았다.

테스트용 Claude 연결 두 개는 제거했다. 기존 서버 설정은 그대로 유지했으며,
검증 중 앱이 변경한 preferences는 보존했다.
합성 DB 및 화면 증거는 로컬 임시 폴더 `/tmp/solo63-macos-20261103`에 보관하며 커밋하지 않는다.
