# ask_paper 단계별 진단

1800초 호스트 종료의 원인은 아직 확정하지 않았다. 모델 로딩뿐 아니라
스레드 대기, DB 조회, 전체 Chroma 검증, 임베딩 계산, 네트워크, 근거 저장을 구분한다.
이 변경은 계측이며 시간 제한·자동 재시도·작업 중단 정책을 바꾸지 않는다.

## 출력

활성 ask_paper/AnswerService 요청에서 stderr로 JSON 한 줄씩 즉시 출력한다.
stdout에는 진단을 쓰지 않는다. 요청별 무작위 request_id와 단계별 span_id를 사용한다.
추가 검색은 같은 request_id, 새로운 span_id로 기록한다.
질문·원문·번역·키·모델 경로·DB ID·예외 메시지·스택은 기록하지 않는다.
필드는 request_id, span_id, stage, event, elapsed_ms, error_code뿐이다.
시간은 단조 시계 기준이며 start는 0, end/error는 해당 단계의 소요 밀리초다.
실패는 STAGE_FAILED, 취소·중단은 CANCELLED로 기록하고 기존 예외를 다시 전달한다.
이 코드는 외부 라이브러리가 자체 출력하는 로그까지 정제하지 않는다.

## 단계

| stage | 범위 |
|---|---|
| ask_paper | MCP 함수 진입부터 응답 변환까지 |
| answer | 작업 스레드의 답변 서비스 진입부터 반환까지 |
| context_lookup / default_context_lookup | context 조회 또는 기본 context 준비 |
| index_lookup | SQLite 색인·청크 스냅샷 조회와 검증 |
| chroma_validation | 컬렉션 열기 및 전체 벡터·본문·메타데이터 검사 |
| query_embedding | 주입된 임베딩 함수 전체 |
| embedding_model_load | 기본 임베더의 라이브러리 import·모델 준비, 캐시 적중 포함 |
| embedding_library_import | sentence_transformers 및 의존 라이브러리 import |
| embedding_model_construct | SentenceTransformer 생성: 디스크 캐시·원격 확인·가중치 초기화 등 |
| embedding_memory_cache_hit | 이미 같은 프로세스에 준비된 모델 재사용 |
| embedding_encode | 실제 임베딩 encode 계산 |
| vector_search | Chroma query 호출 |
| generation | 생성 제공자 전체, 응답 파싱 포함 |
| generation_api_call | HTTP 요청부터 응답 본문 수신까지, HTTP 성공 판정은 아님 |
| evidence_validation | 최종 인용문 조립·구조 검증 |
| evidence_save | SQLite 근거 검증 및 저장 트랜잭션 |

## 재현·해석

서버를 재시작해 새 코드를 로드한 뒤 동일한 요청을 한 번 실행한다.
Claude 호스트의 MCP stderr 로그를 확인하거나, 서버 실행 명령 뒤에
`2> ask-paper-diagnostics.log`를 붙여 별도 파일에 보관한다. stdout과 합치지 않는다.
요청 하나의 request_id로 묶고 start만 있고 end/error가 없는 span을 확인한다.
중첩 단계가 있으므로 가장 안쪽의 미완료 단계를 먼저 본다.

- ask_paper만 시작: 작업 스레드 진입 전 대기 등을 조사한다.
- chroma_validation 미완료: 전체 컬렉션 읽기·검증 단계에 머무른 상태다.
- embedding_model_load 미완료: import·캐시 접근·다운로드·모델 초기화 중일 수 있다.
- embedding_library_import 미완료: 모델 생성 전 라이브러리 import 구간이다.
- embedding_model_construct 미완료: 라이브러리 import는 끝났지만 모델 생성 내부에서 대기 중이다.
  디스크 캐시가 존재해도 완전성이나 원격 확인 생략을 보장하지 않는다.
- generation_api_call 미완료: 네트워크·제공자 응답 대기를 조사한다.
- evidence_save 미완료: SQLite 잠금·저장 등을 조사한다.

시작 로그만으로 근본 원인을 확정할 수는 없다. 강제 프로세스 종료 시 종료 로그가
없을 수 있으며, 호스트가 응답 대기를 끝냈어도 작업 스레드는 계속 실행될 수 있다.
이 로그는 MCP progress 알림이나 주기적 heartbeat가 아니며 호스트 timeout을 연장하지 않는다.
근거 없음·잘못된 생성 결과는 기존 정책대로 처리되므로 저장 단계가 생략될 수 있다.
실제 운영 데이터·유료 API를 자동 재호출하지 않는다.

## 호스트와 독립적으로 stderr를 파일에 저장하기

진행 중인 호출은 이 설정으로 바뀌지 않는다. 종료를 확인한 뒤 다음 실행부터 적용한다.
기존 MCP 설정의 env 등 다른 항목은 유지하고 command·args만 다음으로 바꾼다.
설정 파일은 자동 수정하지 않는다.

```json
{
  "command": "D:\\fd\\soloLeveling\\.venv\\Scripts\\python.exe",
  "args": ["D:\\fd\\soloLeveling\\scripts\\run_mcp_diagnostics.py"]
}
```

이 래퍼는 자식 서버를 따로 띄우지 않고 같은 프로세스에서 서버를 실행한다.
stdin·stdout은 그대로 유지하고 운영체제 stderr 파일 설명자만 실행별 파일로 연결한다.
기존 실행 환경 변수와 작업 디렉터리는 유지하며 소스는 래퍼가 있는 저장소의 src를 사용한다.
로그는 `D:\fd\soloLeveling\runtime\diagnostics\mcp-시각-UUID.log`에 저장된다.
`--log-dir`로 다른 위치를 지정할 수 있다. 파일은 덮어쓰지 않고 자동 삭제하지 않는다.

서버나 API를 실행하지 않고 파일 수집 경로만 점검하는 명령:

```powershell
.\.venv\Scripts\python.exe scripts/run_mcp_diagnostics.py --check
Get-ChildItem .\runtime\diagnostics\*.log | Sort-Object LastWriteTime -Descending
```

시작 기록 diagnostic_startup에는 Python·진단 모듈·서버 모듈 경로와 PID가 담긴다.
이는 소스 선택 확인용이며 서버 초기화 성공을 뜻하지는 않는다.
check 실행은 diagnostic_probe 시작·종료만 기록한다. 실제 재연결 후 생성된 별도 로그에서
ask_paper 단계를 확인한다. 로그를 읽을 때 `Get-Content <로그경로> -Tail 30`을 사용할 수 있다.

**주의:** 단계별 JSON은 본문을 기록하지 않지만, 이 파일은 외부 라이브러리의 stderr도
그대로 수집하므로 민감한 정보나 로컬 경로가 들어갈 수 있다. 전체 파일을 공유하지 말고
request_id·span_id·stage·event·elapsed_ms·error_code 필드만 선별한다.
로그 저장 실패 시 서버 실행을 시작하지 않는다. 로그 폴더는 Git 제외 대상이다.

## import 지연 스택 수집

진단 래퍼로 실행하면 embedding_library_import가 30초 이상 걸릴 때 faulthandler가
모든 Python 스레드의 스택을 한 번 기록한다. 같은 로그 폴더의
`import-stack-요청ID-진단ID.log`이며 요청 ID로 단계 로그와 연결한다.
정상 완료·예외 종료 시 예약을 취소한다. 파일은 예약 시 생성되므로 빠르게 종료하면
빈 파일이 남을 수 있다. 서버나 요청을 강제 종료하지 않는다.

변수 값·소스 본문은 수집하지 않지만 경로·줄 번호·함수명이 포함된다.
네이티브 라이브러리 내부 원인까지 보장하지는 않는다.
전역 타이머이므로 동시 import 중 하나만 감시하고 나머지는 WATCHDOG_BUSY를 기록한다.
다른 dump_traceback_later 예약과 병용하지 않는다. 예약 실패는 WATCHDOG_SETUP_FAILED로
기록하고 import는 계속한다. 현재 호출 종료 후 MCP를 재시작해야 적용된다.

## 메인 스레드 선행 import 비교 옵션

래퍼에 `--preload-embedding-library`를 지정하면 MCP 서버 실행 전에 메인 스레드에서
sentence_transformers를 import한다. 기본값은 꺼짐이다. 모델 생성·다운로드는 요청하지 않는다.
`startup_embedding_import` 시작·종료 시간을 기록하고 30초 지연 시 같은 스택 감시를 적용한다.
`--check`가 함께 있으면 선행 import도 수행하지 않는다.

비교 실행 시 MCP args 예:

```json
["D:\\fd\\soloLeveling\\scripts\\run_mcp_diagnostics.py", "--preload-embedding-library"]
```

시작 시간이 늘어나므로 MCP 연결 제한 시간에 걸릴 수 있다. 먼저 연결과 선행 import 종료 로그를
확인한 뒤 질문을 한 번 호출한다. 성공하더라도 초기화 순서 변경의 우회 효과일 뿐,
DLL 잠금 등 근본 원인을 확정하는 증거는 아니다. BLAS 설정 등 다른 조건은 동시에 바꾸지 않는다.
