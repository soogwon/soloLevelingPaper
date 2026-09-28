# 로컬 MCP 서버 실행

현재 연결 범위는 로컬 PDF 등록·상태 조회와 등록된 논문 질문·근거 재조회다.

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

`job_id`로 `processing`, `ready`, `failed`, `interrupted` 상태와 현재 단계,
제한 사항을 조회한다. `ready`일 때만 `ask_paper`, `get_evidence`를 capabilities에
표시한다. 서버 재시작 시 끝나지 않은 processing 작업은 interrupted가 된다.

### ask_paper

필수 입력은 `context_id`, `question`이다. 선택 입력은 `standalone_question`,
`focus`, `top_k`다. `focus.pdf_pages`는 1부터 시작하는 실제 PDF 페이지이며
`focus.section_ids`와 함께 지정하면 두 조건의 교집합에서 검색한다.

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

- 서버 시작 시 DB 스키마를 준비하고 이전 실행의 processing 작업을 interrupted로 바꾼다.
- 서비스 호출은 작업 스레드에서 실행해 비동기 MCP 메시지 처리를 직접 막지 않는다.
- `standalone_question`이 있으면 검색·생성에 사용하지만 원 질문도 비어 있지 않아야 한다.
- URL 등록, `start_learning`, 요약·가이드는 아직 노출하지 않는다.
- stdio 서버는 단일 로컬 프로필을 전제로 하며 원격 인증은 구현하지 않는다.

테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/mcp tests/integration/test_mcp_stdio.py -v
```
