# #99 worker 종료 API 실패 — B 재현과 A 인계

2026-11-09, 기준 main `615a76e`. #245의 Windows 정상 종료·강제 종료 검증은 이미
main에 반영됐다. 이 문서는 그 검증에서 제외했던 종료 API 자체의 실패를 보충한다.
제품 코드·DB·MCP 입력·보관/삭제 정책은 변경하지 않는다.

## 재현 범위

`tests/unit/test_worker_shutdown_contract.py`는 `ProcessEmbedder`의 기존 요청·종료 경로에
합성 Process/Connection 대역을 주입한다. 요청은 `ok=false` 응답으로 실패하고,
이어지는 `terminate`, `join`, `kill` 중 하나에 OSError를 주입한다. 추가로 terminate와
kill 뒤에도 살아 있다고 보고하는 대역을 사용한다. 모델·외부 API·사용자 로그·실제 자식
프로세스는 사용하지 않는다. 이 결과를 Windows OS 호출이 실제로 실패한 실측으로 간주하지 않는다.

| 주입 | 현재 확인한 결과 | 요청하는 계약 |
|---|---|---|
| terminate 예외 | 원래 `EMBEDDING_UNAVAILABLE` 대신 OSError, IPC 열린 상태, 살아 있는 worker 참조 소실 | 원래 고정 오류 보존, 부모 IPC 정리, 미종료 자원 추적 유지 |
| join 예외 | 원래 오류 대신 OSError, IPC와 Process 객체 정리 생략 | 종료 확인 실패를 성공으로 취급하지 않고 정리 가능한 자원 추적 유지 |
| kill 예외 | 원래 오류 대신 OSError, IPC 열린 상태, 살아 있는 worker 참조 소실 | 원래 고정 오류 보존, 부모 IPC 정리, 미종료 자원 추적 유지 |
| kill 이후에도 생존 | `worker_stop_failed` 기록과 고정 오류는 유지하지만 살아 있는 worker 참조 소실 | 종료 실패 기록에 더해 후속 회수 또는 재생성 차단을 위한 소유권 유지 |

원인은 `process_embedder.py`의 `_stop()`이 시작할 때 `_process`와 `_connection`을
먼저 비운 뒤, 중간 OS 호출의 예외와 관계없이 IPC를 닫는 처리를 두지 않은 데 있다.
호출의 except 절 안에서 `_stop()`이 다시 예외를 내면 원래 요청 오류도 교체된다.
terminate 실패·kill 실패·kill 후 생존의 세 경우에는 후속 요청이 새 spawn context를
확보하려는 것까지 관찰했다. context 확보 직전에 대역으로 차단하므로 실제 새 프로세스는 만들지 않는다.

MCP의 `to_tool_error`는 이 OSError를 본문 없는 `INTERNAL_ERROR`로 변환한다.
따라서 **MCP 응답에 원본 예외가 노출된 것으로 보고하지 않는다.** 변환기 수준에서 합성
민감 문자열 미포함, stdout 미출력, 직렬화 잠금 해제를 별도로 검증한다. 실제 MCP 프로토콜
왕복이나 답변 생성 전체 흐름을 이 테스트로 검증한 것은 아니다.

## 테스트 해석

정상 종료·오류 변환 방어는 일반 테스트로 확인한다. 아직 제품에서 충족하지 못한 계약은
`xfail(strict=True, raises=AssertionError)`로 표시한다. **xfail은 합격이 아니라 재현된 결함**이다.
A 수정으로 XPASS가 발생하면 해당 xfail 표식을 제거하고 일반 회귀 테스트로 전환한다.
소유권 테스트는 현재 `_process` 필드를 기준으로 작성했으며, A가 별도 회수 목록/격리 상태를
도입하면 같은 미종료 자원 추적 불변식을 검증하도록 바꾸어야 한다.

```powershell
python -m pytest tests/unit/test_worker_shutdown_contract.py -q -rx
python -m pytest tests/unit/test_process_embedder.py tests/integration/test_windows_log_lifecycle.py tests/integration/test_windows_mcp_log_bundle.py -q -rs
```

Windows 11 / Python 3.12.14에서 기존 세 파일은 **31 passed**, 최종 신규 계약 파일은
**5 passed, 12 xfailed**였다. 기존 세 파일과 초기 신규 사례를 함께 실행한 결과는
36 passed, 9 xfailed(82.48초)였고, 추가한 후속 spawn 차단 세 사례를 포함해 신규 파일을
다시 실행한 결과는 5 passed, 12 xfailed(2.67초)였다. 기존 제품 테스트의 새로운 실패는 없었다.
12건의 xfail은 종료 오류 보존 3건, IPC 정리 3건, 미종료 소유권 3건, 추가 spawn 차단 3건이다.
이는 대역에 의한 결함 재현이며 실제 Windows 종료 호출 실패나 wheel 설치본의 신규 검증 결과는 아니다.

## A 확인 요청과 남은 C 범위

1. **A — 오류와 정리:** 정리 오류가 원래 요청 오류를 덮어쓰지 않도록 하고, 한 단계가 실패해도
   독립적으로 정리 가능한 부모 IPC·종료된 Process 핸들의 정리를 시도한다. 원본 예외는 재로깅하지 않는다.
2. **A — 소유권:** 프로세스가 종료됐음을 확인하기 전에 회수할 마지막 참조를 버리지 않는다.
   종료 확인 실패 시 후속 요청에서 worker를 계속 추가 생성하지 않도록 재사용 금지·회수·격리
   정책을 확정한다. 무제한 종료 재시도나 다른 프로세스 종료는 하지 않는다.
3. **A — 로그 생산자 연결:** 새 묶음 규약의 잠금/정상 종료 기록 연결은 아직 별도 구현이다.
   모든 writer 종료를 확인하지 못한 상태에서는 `.closed.json`을 게시하지 않는다. 현재 구형 worker
   로그의 unknown 보존과 삭제 비활성 정책은 유지한다.
4. **C — 실제 Desktop:** Windows 앱 정상 종료·강제 종료·재시작 검증은 여전히 남아 있다.
   이 합성 대역 검증이나 #245의 실제 stdio 테스트로 대체하지 않는다.

이 변경은 B의 재현·인계용 테스트와 문서다. A의 제품 파일 수정, 실제 API 호출, Desktop 재설치,
기존 로그 정리는 포함하지 않는다. #99는 열린 상태로 유지한다.
