# #57 Claude Desktop MCP App UI 기술 검증

## 범위와 현재 판정

기존 `get_paper_status(job_id)` 결과에 읽기 전용 상태 카드를 연결한다.
도구 수·입출력·오류 형식은 #47의 5개 도구 계약을 유지한다.
요약·가이드·퀴즈 화면, 재등록·재번역, HTTP 서버·원격 배포는 이번 범위가 아니다.

**자동 테스트와 실제 Claude Desktop 검증은 별개다. 실제 호스트에서 렌더링을 확인하기 전에는 #57을 완료 처리하지 않는다.**

| 항목 | 구현·확인 방법 |
|---|---|
| UI 리소스 | `ui://solo-leveling/paper-status.html` |
| MIME | `text/html;profile=mcp-app` |
| 도구 연결 | `get_paper_status._meta.ui.resourceUri` |
| 표시 상태 | 연결·로딩·빈 결과·정상 처리·실패·중단·오류 |
| 새로고침 | 사용자가 누를 때 동일 job_id로 get_paper_status만 호출 |
| 폴백 | UI 미지원 호스트에서도 기존 structuredContent와 JSON text 응답 유지 |
| 민감 데이터 | 논문 본문·API 키를 HTML에 넣지 않음. 데이터는 textContent로 표시 |
| 외부 접근 | SDK를 HTML에 포함. 외부 CDN·브라우저 직접 API·DB 접근 없음 |

## 구현 구조

- `interfaces/mcp/status_app.py`: 리소스 URI·MIME·CSP 등록과 패키지 HTML 읽기
- `interfaces/mcp/server.py`: 기존 상태 도구에 UI 메타데이터 연결
- `ui/paper-status/`: HTML·JS 원본, 고정 버전 의존성과 잠금 파일, 빌드·DOM 테스트
- `interfaces/mcp/assets/paper-status.html`: 배포용 생성 파일. 직접 수정하지 않고 빌드로 갱신
- `pyproject.toml`: wheel에도 HTML 포함

서버는 UI 지원 여부에 따라 기존 응답을 제거하지 않는다. 호스트가 메타데이터를 무시해도 텍스트로 동일한 상태를 조회할 수 있다.
조회 실패 시 서버의 기존 오류 처리를 유지한다. 화면이 이미 열려 있으면 일반 오류 안내를 표시하지만, 최초 호출 오류에서 호스트가 UI를 열지 않는 경우에는 채팅 오류 안내를 사용한다.
자동 폴링이나 자동 재시도는 하지 않는다. 화면 대기 시간 초과는 서버 작업 실패 판정이나 서버 요청 취소를 의미하지 않는다.

## 개발 및 자동 테스트

이미 빌드된 HTML을 사용하면 Python 서버 실행에 Node.js가 필요하지 않다.
UI를 수정할 때만 Node.js 및 npm이 필요하다. 개발 확인 환경은 Node 24 계열이다.

```powershell
cd D:\fd\soloLeveling\ui\paper-status
npm ci --ignore-scripts
npm test
npm run build
cd ..\..
.\.venv\Scripts\python.exe -B -m pytest tests/unit/mcp tests/integration/test_mcp_stdio.py tests/integration/test_mcp_workflow.py -q
```

`npm test`는 호스트 연결을 모의 처리한 DOM 테스트다. 실제 SDK-호스트 핸드셰이크나 화면 렌더링 성공을 증명하지 않는다.
Python stdio 테스트는 리소스 전달·MIME·메타데이터와 기존 텍스트 계약을 확인한다.

## 실제 Claude Desktop 확인 순서

1. Claude Desktop 앱 버전, OS, 테스트 시각을 아래 기록표에 적는다. Claude Code CLI와 구분한다.
2. 기존 등록 작업이 끝난 뒤 Claude Desktop의 해당 MCP 서버를 재연결한다. 설정을 다른 서버나 원격 연결로 자동 변경하지 않는다.
3. 실제 존재하는 `job_id`로 아래 요청을 보낸다. 재등록은 필요하지 않다.

   > solo-leveling-paper의 get_paper_status를 job_id="기존 작업 ID"로 한 번 호출해 줘. 상태 카드가 제공되면 표시해 줘. 논문을 다시 등록하거나 번역하지 마.

4. '논문 처리 상태' 카드가 보이는지, 단계·작업 ID·논문 버전·제한 사항이 도구 응답과 같은지 확인한다.
5. '상태 새로고침'을 한 번 누른다. 기존 작업 상태만 조회하고 새 작업·context가 만들어지지 않는지 확인한다.
6. 준비 완료가 전체 번역 완료를 뜻하지 않는다는 안내를 확인한다.
7. 실패·빈 결과는 자동 DOM 테스트로 먼저 확인한다. 실제 호스트에서는 존재하지 않는 작업 ID로 별도 조회하여 안전한 오류 안내를 확인할 수 있다. 실제 논문 데이터를 지우거나 실패시키지 않는다.
8. UI 미지원 클라이언트 또는 기존 stdio 테스트에서 동일 응답을 텍스트로 확인한다.

카드가 안 보인다고 등록·임베딩 실패로 단정하지 않는다. 도구 메타데이터·리소스 조회·호스트의 MCP Apps 지원을 순서대로 확인한다. 일반 브라우저에서 HTML을 직접 열면 호스트 연결이 없으므로 실제 호스트 검증이 아니다.

### 호스트 검증 기록

| 항목 | 결과 |
|---|---|
| Claude Desktop 버전 / OS / 확인 시각 | 미확인 |
| 상태 카드 실제 렌더링 | 미확인 |
| 도구 응답과 카드 값 일치 | 미확인 |
| 버튼에서 상태 재조회 | 미확인 |
| 실제 호스트 오류 안내 | 미확인 |
| UI 없는 클라이언트의 텍스트 응답 | 자동 stdio 테스트로 확인, 수동 확인 별도 |

호스트 검증 완료 후 결과를 기록하고 #57의 완료 여부를 판단한다.

## 공식 참고 자료

- [MCP Apps 빠른 시작](https://github.com/modelcontextprotocol/ext-apps/blob/main/docs/quickstart.md)
- [MCP Apps 명세](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)
- [Python 서버와 Claude Desktop stdio 예제](https://github.com/modelcontextprotocol/ext-apps/blob/main/examples/say-server/README.md)

호스트·SDK 지원은 버전에 따라 달라질 수 있으므로 실제 Desktop 버전을 기록한다.
