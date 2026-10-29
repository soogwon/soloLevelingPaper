# A와 협의할 임베딩 로그 개인정보 보호 (#64)

## 통합 계약과 검증 범위 (2026-10-28)

A의 PR #107이 main에 병합되었다. C 런타임은 최종 설정을 `ProcessEmbedder`의
`collect_raw_logs` 불리언 인자로 전달한다. A의 작업 프로세스 구현은 추가 수정하지 않는다.

| 진입점 | 원본 수집 결정 |
| --- | --- |
| Desktop `serve`, 진단 실행기 | `--collect-raw-logs` 유무가 최우선. 환경에 `1` 또는 `0`을 설정해 자식까지 전달 |
| 일반 MCP 실행 | 프로세스 환경 > `.env` > 기본 비수집. `SOLO_LEVELING_COLLECT_RAW_LOGS`가 정확히 `1`인 경우만 수집 |
| 직접 `ProcessEmbedder` 호출 | 명시적 불리언 인자 우선, 생략 시 A 구현의 프로세스 환경 판정 |

CLI 종료 시 기존 프로세스 환경값을 복원한다. 실행 중인 자식의 정책 변경은 지원하지 않으며
옵션 변경 후 서버를 재시작한다. 기본 모드 자식 로그는 `worker_enter`의 worker ID와 PID만
남긴다. Python 및 네이티브 stdout·stderr와 워치독 스택은 버린다. 자식 단계별 시간은
기본 모드에 없으며 `SafeDiagnosticStream`을 공통 계층으로 이동하는 작업은 포함하지 않는다.
일반 MCP 직접 실행의 부모 stderr에는 진단 실행기의 필터가 적용되지 않는다.

원본 수집은 자동 마스킹을 보장하지 않는다. 해제 후 재시작해도 기존 로그는 유지된다.
로그를 공유하기 전 사용자가 내용을 확인해야 하며, 보관 기간·자동 삭제는 #99에서 다룬다.

`tests/integration/test_embedding_log_privacy.py`는 Desktop·진단·직접 실행 설정을 사용해
실제 자식 프로세스에 합성 민감정보를 출력한다. 정상·실패·시간 초과 및 종료 후
기본 비수집/옵트인 수집, stdout 격리를 검증한다. 서버 실행 부분은 대역이며 실제 MCP
프로토콜 회귀는 `tests/integration/test_mcp_stdio.py`로 별도 확인한다.
설정 객체에서 생성자까지의 전달은 `tests/unit/mcp/test_runtime.py`에서 검증한다.
실제 모델·API·Claude 호출과 다른 OS 검증 결과로 확대 해석하지 않는다.

### Windows 검증 결과 (2026-10-28)

- `tests/integration/test_embedding_log_privacy.py`: 19개 통과. 상세 수집 해제 후
  별도 프로세스로 재시작하여 신규 원본 비수집 및 기존 파일 보존도 확인했다.
- runtime·Desktop·진단 실행기·ProcessEmbedder 단위 테스트와 실제 MCP stdio 통합:
  106개 통과. Python 3.12.14, 합성 자료·가짜 작업 사용.
- 제한 환경에서 일부 회귀 실행이 정체되어 중단하고 전용 임시 경로와 정상 실행 권한으로
  위 106개를 재실행했다. 초기 통합 테스트의 자식 불필요 import로 스택 기록 전에 제한
  시간이 끝나는 문제는 테스트에서 해당 import를 서버 대역 안으로 옮겨 해결했다.
- A가 보고한 `test_answer_expansion.py::test_expands_once_with_same_context_and_scope_and_saves_only_final`
  실패는 별도 재현됐다. 기본 모드의 `evidence_trace` 비출력과 테스트 기대의 불일치이며
  이번 C 변경에서 해당 파일이나 B 진단 정책은 수정하지 않았다.
- Mac/Linux·실제 모델 실행은 미검증이다. 전체 테스트 통과 또는 #64 전체 완료로
  기록하지 않으며, 원본 로그 보관·자동 삭제는 #99 후속 범위다.

### macOS 검증 결과 (2026-10-29)

- 기준: PR #111 병합 이후 main `46b7c622c888135a588506bc68650db5a3a2159a`.
- 환경: macOS 26.7.1 (25G241), arm64, Python 3.12.14.
- 로그 통합 19개와 runtime·Desktop·진단 실행기·ProcessEmbedder·실제 MCP stdio
  회귀를 함께 실행해 **124개 통과, 1개 건너뜀**을 확인했다. 건너뛴 항목은
  Windows 전용 `test_spawn_while_stdin_pipe_is_being_read`다.
- 부모 안전 로그 필터·진단·답변 진단 테스트는 **117개 통과**했다.
- 합성 문자열을 Python stdout/stderr와 네이티브 fd에 출력하는 실제 자식 프로세스로
  정상·실패·시간 초과, 기본 비수집과 명시적 원본/스택 수집, 부모 출력 격리를 확인했다.
  수집 옵션 해제 후 별도 프로세스로 재시작하면 새 원본 수집이 중단되고 기존 파일은
  그대로 유지됐다. 활성 요청 종료 및 환경변수의 정확한 값 판정도 회귀 범위에 포함한다.
- 추가로 임시 설정·빈 DB에서 실제 `desktop serve`를 기본/상세 모드로 각각 실행하여
  MCP 초기화, 8개 도구 목록, 빈 작업 목록 조회, 종료를 확인했다. 설정 파일에 원본 수집
  `1`이 있어도 옵션 없는 Desktop 실행의 시작 로그는 `raw_logs=false`였고,
  명시적 옵션 실행은 `true`였다. 이 추가 점검은 임시 실행 스크립트로 수행했다.
- 사용자 논문·DB·API 키, 모델 다운로드와 유료 API는 사용하지 않았다. 실제 Claude
  Desktop 앱의 연결 설정도 변경하지 않았다. Desktop 실행 명령의 프로토콜 점검을
  실제 Claude UI 검증이나 실제 모델 작업 검증으로 확대하지 않는다.

재현 명령(저장소 루트, 개발 환경의 Python 사용):

```bash
python -m pytest tests/integration/test_embedding_log_privacy.py tests/unit/mcp/test_runtime.py tests/unit/mcp/test_desktop.py tests/unit/test_mcp_diagnostic_runner.py tests/unit/test_process_embedder.py tests/integration/test_mcp_stdio.py -q --tb=short
python -m pytest tests/unit/test_safe_diagnostic_stream.py tests/unit/test_diagnostics.py tests/integration/test_answer_diagnostics.py -q --tb=short
```

전체 테스트를 다시 실행한 결과는 아니다. 아래 추가 검증 전에는 Linux·실제 Claude 호스트 검증이 남아 있었고,
기본 자식 로그의 `worker_enter` 외 단계·소요 시간·안전 오류 코드 추가는 별도 합의가
필요하다. 일반 MCP 부모 stderr의 필터 적용 제한과 #99의 보관·삭제 범위도 유지한다.
따라서 이번 Mac 검증만으로 #85나 #64 전체를 종료하지 않는다.

### 실제 Claude Desktop 추가 검증 (2026-10-29)

- 동일 main 소스를 사용하는 임시 `solo85-host-check` 서버를 Claude Desktop에 연결했다.
  Desktop CLI와 진단 실행기는 실제 코드를 사용하되, 서버 생성 지점은 검증용 FastMCP로
  대체했다. 실제 `ProcessEmbedder`와 자식 로그 래퍼에 가짜 임베딩 작업을 주입했으며,
  모델·유료 API·실제 논문은 사용하지 않았다. 제품 전체 등록 흐름 검증은 아니다.
- Claude를 완전히 종료·재실행하면서 기본 → 명시적 원본 수집 → 옵션 해제 순서로
  각각 새 서버와 자식 프로세스를 실행했다. 환경·설정 파일의 수집 값은 `1`로 유지하여
  Desktop CLI 옵션 유무가 우선 적용되는지도 확인했다.

| 단계 | Python·네이티브 합성 문자열 | 스택 파일 생성 | 기존 자식 로그 해시 |
| --- | --- | --- | --- |
| 기본 | 신규 로그에 없음 | 없음 | 유지 |
| 원본 수집 옵션 사용 | 신규 원본 로그에 있음 | 있음 | 유지 |
| 옵션 해제 후 재시작 | 신규 로그에 없음 | 없음 | 유지 |

- Claude에서 `get_log_privacy_report`를 한 번 호출하여 세 단계의 통과 결과가
  채팅 표로 표시되는 것을 확인했다. 이 도구는 결과 불리언만 반환하며 로그 본문이나
  키를 반환하지 않는다. Claude의 임시 서버 로그에도 합성 원본 문자열은 없었다.
- 정상 완료 작업이므로 스택 파일 생성 여부만 확인했다. 실제 지연 스택 내용,
  실패·시간 초과·활성 작업 종료는 앞선 자동 테스트의 검증 범위다.
- 임시 서버 연결을 제거하고 Claude를 다시 실행했다. 기존 MCP 서버 설정은 백업과
  동일함을 확인했다. 합성 검증 자료와 화면 캡처는 저장소 밖 임시 폴더에 보관했다.
- 남은 항목: Linux 검증, 기본 단계 진단 추가 범위·담당 합의. #99의 보관·정리와
  일반 MCP 부모 stderr 필터 제한은 별도이며 #85·#64는 자동 종료하지 않는다.

아래는 통합 전 협의 당시 기록이다.

## 확인한 현재 동작

- `infrastructure/embeddings/worker.py`의 `diagnostic_worker()`는 라이브러리의
  stdout·stderr를 `worker-*.log`에 수집하고 지연 스택을 별도 파일에 기록한다.
- `infrastructure/embeddings/process_embedder.py`는 이 진단 작업 프로세스를 생성한다.
- 예외 메시지를 IPC로 전달하지 않는 방어는 있지만, 라이브러리가 직접 출력한 내용은
  별도 로그에 남을 수 있다. 실제 키나 논문 내용의 유출을 확인했다는 뜻은 아니다.

## 협의할 변경

1. 기본 실행에서는 원본 라이브러리 출력·스택을 저장하지 않고 제한된 진단 정보만 남긴다.
2. 상세 수집이 필요한 경우 명시적 설정을 임베딩 프로세스까지 전달한다.
3. stdout 격리, 프로세스 종료·시간 제한, 문제 진단 기능의 회귀를 검증한다.
4. 합성 민감정보를 Python 출력과 네이티브 파일 설명자로 출력해 기본 로그에 남지 않는지 검증한다.

설정 계약은 A와 조율 후 결정한다. 현재 A 파일은 수정하지 않았다. C 진단 실행기의
`--collect-raw-logs`는 부모 프로세스용 옵션이며 자식 프로세스까지 적용되지 않는다.
따라서 C 보완만으로 #64의 전체 로그 비노출 완료 조건을 충족했다고 판단하지 않는다.
