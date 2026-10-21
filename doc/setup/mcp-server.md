# 로컬 MCP 서버 실행

저장소 없이 설치하는 경우 [Claude Desktop 설치·배포](desktop-installation.md)를 참고한다.

현재 연결 범위는 로컬 PDF 등록·상태 조회와 등록된 논문 질문·근거 재조회다.

입출력 필드·기본값·nullable 값·도구 속성·호환성 기준은
[MCP 인터페이스 명세](../architecture/10-mcp-contracts.md)를 기준으로 한다.
이 문서는 실행과 호스트 사용 안내에 집중한다.

실제 자료를 등록하기 전에 [외부 전송 데이터·시점 안내](external-data-transfer.md)를 확인한다.
`LOCAL_ONLY`는 서버의 OpenAI 호출을 차단하지만 Claude 호스트 통신까지 차단하지 않는다.

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

## 선택적 진단 로그 수집 (#64)

`python -m solo_leveling.interfaces.cli.mcp_diagnostics` 또는
`python scripts/run_mcp_diagnostics.py` 실행 시 기본 로그에는 시작 메타데이터와
고정 실패 코드와 허용된 단계별 JSON 로그를 저장한다. 단계 로그의 요청·구간 ID,
단계명, 이벤트, 소요 시간, 고정 오류 코드만 검증해 보존한다. 임의 문자열·추가 필드와
네이티브 원본 stderr는 폐기하고 import 지연 스택은 기본 수집하지 않는다.
근거별 상세 추적 및 작업 프로세스 이벤트는 기본 필터의 대상이 아니다.
새 단계명을 추가하면 C의 `safe_diagnostic_stream.py` 허용 목록도 함께 갱신해야 한다.
stdout의 MCP 통신은 유지한다.

문제 재현에 상세 정보가 필요한 경우에만 `--collect-raw-logs`를 추가한다.
이 모드는 원본 stderr, 실행 파일·모듈·로그의 절대 경로, import 지연 스택 수집을
허용한다. 라이브러리가 출력한 키·본문·사용자 경로 등이 포함될 수 있고 자동 마스킹을
보장하지 않는다. 합성 자료로 재현하고 공유 전에 내용을 확인한다. 로그의 자동 삭제는 없다.
다시 기본 모드로 실행해도 이미 수집한 파일은 삭제되지 않는다.

이 정책은 C 진단 실행기를 통해 시작한 부모 프로세스에만 적용된다. 일반 MCP 실행이나
A의 임베딩 자식 프로세스가 별도 파일에 수집하는 로그까지 보호하는 설정은 아니다.
남은 범위는 [임베딩 로그 협의 사항](embedding-log-privacy-coordination.md)을 참고한다.

## 공용 PDF 저장 인터페이스 (#28)

`infrastructure.storage.pdf_store.PdfStore`는 MCP에 의존하지 않는 저장 모듈이다.
기존 `LocalPdfStore`는 import 루트·상대 경로·확장자 검증과 요청 fingerprint 생성을 유지하고,
PDF 내용의 검사·저장은 이 공용 모듈에 위임한다. 기존 MCP 오류 응답은 유지한다.

```python
from solo_leveling.infrastructure.storage.pdf_store import PdfStore

# managed_root에는 공통 설정의 DATA_ROOT/PDF_SUBDIR 경로를 전달한다.
store = PdfStore(managed_root, max_bytes)
stored = store.save_bytes(downloaded_pdf_bytes)
# 파일·동기 바이너리 스트림은 store.save_stream(stream)으로 전달할 수 있다.
# stored.path, stored.file_hash, stored.size_bytes를 후속 등록에서 사용한다.
```

- SHA-256 기반 `<hash>.pdf`를 저장하며 동일 내용은 기존 파일을 검증한 뒤 재사용한다.
- `%PDF-` 헤더와 크기만 확인한다. 실제 PDF 파싱·암호화·손상 여부 검증을 대신하지 않는다.
- 스트림은 현재 위치부터 읽으며 저장소가 닫거나 되감지 않는다. 블로킹 바이너리 스트림을 전달한다.
- 호출자가 지정한 저장소는 신뢰된 설정 경로여야 한다. 파일명·URL을 저장 경로로 사용하지 않는다.
- 다운로드·네트워크 제한·원본 파일명·요청 키·DB 등록·논문 ID 정책은 호출자 책임이다.
  `save_bytes`는 이미 메모리에 있는 데이터를 받으므로 다운로드 중 용량 제한을 대신하지 않는다.
- 잘못된 헤더·용량은 공용 `UnsupportedDocumentError`, 읽기·저장 오류는 예외로 전달한다.
  실패 시 이번 호출의 임시 파일만 정리하며, 기존 관리 파일은 덮어쓰거나 삭제하지 않는다.
- 동일 내용의 동시 저장은 원자적 하드 링크 생성으로 처리한다. 관리 저장소 파일시스템은
  하드 링크를 지원해야 한다(예: NTFS). 미지원·권한 오류 시 저장에 실패하며 덮어쓰기 방식으로 우회하지 않는다.
  프로세스 강제 종료 후 남은 임시 파일이나 DB 등록 실패 후 남은 파일의 정리는 별도 정책이다.

#28에서는 공용 인터페이스와 로컬 입력 연결까지만 제공한다.
`workers/ingestion.py`의 URL 등록은 아직 변경하지 않았으며, #27에서 A가 위 인터페이스를 연결한다.
기존 UUID 파일 이관·정리와 URL 등록 시 관리 경로 설정도 #27에서 결정한다.

## 도구

### list_paper_jobs

기존 작업 ID를 모르면 인자 없이 호출하거나 `query`로 제목·파일명을 검색한다.
`limit`은 기본 20·최대 100이며 `next_cursor`가 있으면 같은 query와 cursor로 다음 페이지를 요청한다.
실패·재처리 작업을 포함한 작업별 목록이며, 논문별 하나로 합친 목록은 아니다.
사용자가 원하는 항목을 선택하면 그 job_id로 `get_paper_status`를 호출한다.
준비 상태를 확인한 뒤 해당 version_id로 `start_learning`을 호출하고,
반환된 context_id를 후속 `ask_paper`에 사용한다.
이 흐름으로 #21의 작업 ID 찾기와 #23의 학습 논문 목록 조회·선택을 지원한다.
읽기 전용이므로 논문 재등록·번역·모델 호출은 필요하지 않다.

수동 확인 예:

> 등록한 논문 작업을 list_paper_jobs로 찾아줘. 논문 재등록이나 번역은 하지 마.
> 후보가 여러 개면 선택할 수 있도록 파일명·상태·작업 ID를 보여줘.

DB가 비어 있으면 빈 목록이 정상이다. 커서 형식과 응답 필드는 MCP 인터페이스 명세를 참고한다.

### add_paper

`source={"kind":"local_file","relative_path":"paper.pdf"}`와 `request_key`를 받는다.
파일은 `DATA_ROOT/IMPORT_SUBDIR` 아래의 상대 경로여야 한다. 절대 경로, `..`, 루트
밖으로 향하는 링크, PDF가 아닌 파일과 `MAX_UPLOAD_BYTES` 초과 파일은 거부한다.
검증한 파일은 `DATA_ROOT/PDF_SUBDIR`에 해시 이름으로 복사하며 원본은 변경하지 않는다.

입력 경로는 OS와 무관하게 Windows 드라이브·UNC·루트 기준 경로와 역슬래시로
표현한 `..`도 거부한다. NUL 문자와 콜론(`:`)도 허용하지 않는다. 콜론 차단은
Windows 대체 데이터 스트림 접근을 막기 위한 것으로 POSIX 파일명에도 동일하게 적용한다.
일반 하위 폴더와 한글 파일명은 지원한다. PDF 헤더 검사는 문서 전체의 유효성이나
악성 콘텐츠가 없음을 보장하는 검사가 아니다.

현재 경계 검사는 신뢰할 수 있는 로컬 사용자가 관리하는 폴더를 전제로 한다.
다른 프로세스가 경로 검사와 파일 열기 사이에 파일·디렉터리를 교체하는 경쟁 상황이나
하드링크의 원본 위치까지 차단하는 보장은 아니다. 신뢰하지 않는 사용자에게 데이터
폴더 쓰기 권한을 제공하지 않아야 한다. 이 경계까지 강화하려면 별도 설계와 공통
저장소 담당 A의 검토가 필요하다. #66의 저장 경로 설정 검증과 이번 PDF 입력 경로
검증은 별개의 검사다.

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
