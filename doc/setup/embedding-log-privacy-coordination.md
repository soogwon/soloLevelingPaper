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
