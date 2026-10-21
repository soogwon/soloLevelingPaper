# 로컬 MCP 서버 실행

저장소 없이 설치하는 경우 [Claude Desktop 설치·배포](desktop-installation.md)를 참고한다.

현재 연결 범위는 로컬 PDF 등록·상태 조회와 등록된 논문 질문·근거 재조회다.

입출력 필드·기본값·nullable 값·도구 속성·호환성 기준은
[MCP 인터페이스 명세](../architecture/10-mcp-contracts.md)를 기준으로 한다.
이 문서는 실행과 호스트 사용 안내에 집중한다.

## 설치와 실행

PowerShell에서 저장소 루트로 이동하고 로컬 의존성을 설치한다.

```powershell
cd D:\fd\soloLeveling
.\.venv\Scripts\python.exe -m pip install -e ".[local]"
.\.venv\Scripts\python.exe -m solo_leveling.interfaces.mcp
```

기본 설정 파일은 실행 디렉터리의 `.env`다. 다른 파일은 MCP 호스트 프로세스 환경에
`SOLO_LEVELING_ENV_FILE`을 지정한다. 프로세스 환경은 파일의 같은 이름 설정보다
우선한다. `DATA_ROOT` 아래에 SQLite와 Chroma가 생성되며 하위 경로가 루트 밖으로
벗어나면 시작을 거부한다.

stdio의 stdout은 MCP 메시지 전용이다. 서버를 터미널에서 직접 실행하면 입력을
기다리는 것이 정상이며, 일반적으로 MCP 호스트가 자식 프로세스로 실행해야 한다.

## 도구

### add_paper

`source={"kind":"local_file","relative_path":"paper.pdf"}`와 `request_key`를 받는다.
파일은 `DATA_ROOT/IMPORT_SUBDIR` 아래의 상대 경로여야 한다. 절대 경로, `..`, 루트
밖으로 향하는 링크, PDF가 아닌 파일과 `MAX_UPLOAD_BYTES` 초과 파일은 거부한다.
검증한 파일은 `DATA_ROOT/PDF_SUBDIR`에 해시 이름으로 복사하며 원본은 변경하지 않는다.

요청은 관리 복사와 DB 작업 확보가 끝나면 반환하고, 파싱·번역·색인은 최대 한 개의
백그라운드 작업으로 진행한다. 동일 `request_key`와 동일 입력은 기존 작업을 반환하며,
다른 입력에 같은 키를 사용하면 `CONFLICT`다.

### get_paper_status

MCP Apps 지원 호스트에는 읽기 전용 상태 카드를 연결한다. 기존 입력·응답은 그대로이며
UI 미지원 호스트도 텍스트로 조회할 수 있다. [Claude Desktop UI 검증](mcp-app-ui.md)을 참고한다.

`job_id`로 `processing`, `ready`, `failed`, `interrupted` 상태와 현재 단계,
제한 사항을 조회한다. `ready`일 때만 `start_learning`을 capabilities에
표시한다. 서버 재시작 시 끝나지 않은 processing 작업은 interrupted가 된다.

`ready`는 성공한 번역 구간의 검색 준비가 끝났다는 뜻이며 전체 번역 완료를 보장하지 않는다.
`limitations`에 번역 실패가 있으면 해당 구간은 검색에서 제외된다.
`provider_unavailable`만으로 연결 실패·시간 초과 등의 세부 원인을 단정하지 않는다.

### start_learning

`version_id`와 선택 입력 `goal`, `known_concepts`를 받는다.
목적은 `understand`(기본값, 이해), `implement`(구현), `skim`(훑어보기)다.
기존 지식은 사용자의 자기보고로 전달하며 생략하면 빈 목록이다.
같은 논문·색인·설정의 context를 재사용하고 다른 설정이면 별도 context를 만든다.
응답의 `context_id`, `goal`, `known_concepts`를 확인하고 후속 질문에 같은 ID를 사용한다.
목적 저장이 생성 프롬프트에 목적별 설명 방식을 적용한다는 보장은 아니다.
이 도구의 `request_key`, 요약 및 `summary_status` 출력은 아직 연결하지 않았다.
별도 요약·읽기 가이드 내부 서비스의 존재와 MCP 도구 노출은 구분한다.

### ask_paper

필수 입력은 `context_id`, `question`이다. 선택 입력은 `standalone_question`,
`focus`, `top_k`다. `focus.pdf_pages`는 1부터 시작하는 실제 PDF 페이지이며
`focus.section_ids`와 함께 지정하면 두 조건의 교집합에서 검색한다.
`top_k`는 기본 5이며 1~20의 정수다. `focus`, `standalone_question`은 생략하거나
`null`로 보낼 수 있다. 두 값이 없으면 기존 context 범위에서 원 질문을 사용한다.

답변은 `context_id`, `verification_level`, `status`, `answer_ko`, `claims`,
`citations`, `reason_code`를 최상위에 반환한다. 현재 검증 수준은
`structural_only`다. 이 도구는 최종 근거를 SQLite에 저장하고 외부 생성 API를
호출할 수 있으므로 read-only 도구가 아니다.

### get_evidence

`context_id`와 비어 있지 않은 `evidence_ids`를 받는다. 다시 검색하거나 생성하지
않고 저장된 한국어 번역, 추출 원문, 페이지, 파일 표시 이름을 반환한다. 다른
context의 근거는 조회할 수 없다.

## 오류와 현재 제한

오류는 `INVALID_ARGUMENT`, `NOT_FOUND`, `CONFLICT`, `UNSUPPORTED_DOCUMENT`,
`PAPER_NOT_READY`, `UPSTREAM_UNAVAILABLE`, `INTERNAL_ERROR`로 구분한다. 원래 예외 메시지,
API 키, 절대 경로와 스택은 MCP 오류에 포함하지 않는다.

위 설명은 서비스 예외 변환에 해당한다. 필수 입력 누락·타입 오류는 함수 실행 전에
SDK가 검사하며, 현재 코드 접두사가 통일되지 않은 검증 문구를 반환한다.
호스트는 `isError`를 먼저 확인하고, 입력값을 포함할 수 있는 SDK 오류 전문을
무조건 기록하지 않는다. `partial` 등 답변 상태는 도구 오류와 구분한다.

- 서버 시작 시 DB 스키마를 준비하고 이전 실행의 processing 작업을 interrupted로 바꾼다.
- 서비스 호출은 작업 스레드에서 실행해 비동기 MCP 메시지 처리를 직접 막지 않는다.
- `standalone_question`이 있으면 검색·생성에 사용하지만 원 질문도 비어 있지 않아야 한다.
- URL 등록, 요약·가이드는 아직 노출하지 않는다.
- stdio 서버는 단일 로컬 프로필을 전제로 하며 원격 인증은 구현하지 않는다.

테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/mcp tests/integration/test_mcp_workflow.py tests/integration/test_mcp_stdio.py -v
```

단위 테스트는 스키마·기본값·기존 입력 호환성·도구 속성을 확인한다.
stdio 테스트는 실제 실행 구성의 도구 목록·오류와, 모의 서비스를 연결한 정상 응답을
확인한다. 외부 생성 API를 호출하거나 실제 사용자 DB를 사용하지 않는다.

## 호스트 안내와 근거 확인

서버 초기화의 instructions와 각 도구 설명에 다음 사용 지침을 제공한다.

- 반환 식별자를 이어서 전달해 등록 → 상태 확인 → 학습 시작 → 질문 → 근거 조회를 수행한다.
- `ok`는 답변 완전성 보증이 아니다. `structural_only`는 의미·수식 정확성 검증이 아니다.
- 문장이 끊기면 같은 context와 반환된 evidence_id로 `get_evidence`를 확인한다.
  저장된 청크 자체가 잘렸다면 이 조회로 누락 문장이 복구되지는 않는다.
- 사용자 지정 범위를 유지하며, 필요한 문맥이 범위 밖이면 범위 확장을 안내한다.
- 인쇄 페이지가 없으면 'PDF 기준 N페이지'로 안내한다.
- 호스트 권한 검사 실패와 MCP 서버 오류를 구분한다. DB·PDF 직접 조회로 우회하지 않고
  실패 사실을 안내한다. 재호출할 때는 기존 context를 유지한다.
- 이전 근거 또는 배경지식을 사용하는 경우 이번 호출에서 얻은 근거와 구분한다.

이 지침은 호스트에 전달하는 사용 안내이며 별도 셸·파일 권한을 강제 차단하는 기능은 아니다.
청크 분할, 인접 근거 보충, 의미 검증은 A·B의 후속 개선이 필요하다.
상세 계획은 [담당별 진단 문서](evidence-boundary-diagnosis-and-ownership.md)를 참조한다.

Claude Code는 등록된 프로젝트 폴더에서 실행한다. 코드를 갱신한 후 기존 MCP 프로세스를
재연결하거나 Claude Code 세션을 다시 시작해 새 instructions와 도구 설명을 받는다.
등록 작업이 진행 중이면 완료 후 재시작한다.
같은 context로 질문하고 `get_evidence`까지 호출해 페이지·인용문·한계 안내를 확인한다.
