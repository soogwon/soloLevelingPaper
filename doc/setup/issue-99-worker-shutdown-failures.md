# #99 worker 종료 API 실패 — 재현과 A 보완

2026-11-09, #245의 Windows 정상 종료·강제 종료 검증과 #255의 종료 실패 재현을
기준으로 A의 프로세스 종료 경계를 보완했다. DB·MCP 입력·보관/삭제 정책은 변경하지 않는다.

## 재현 범위

`tests/unit/test_worker_shutdown_contract.py`는 `ProcessEmbedder`의 기존 요청·종료 경로에
합성 Process/Connection 대역을 주입한다. 요청은 `ok=false` 응답으로 실패하고,
이어지는 `terminate`, `join`, `kill` 중 하나에 OSError를 주입한다. 추가로 terminate와
kill 뒤에도 살아 있다고 보고하는 대역을 사용한다. 모델·외부 API·사용자 로그·실제 자식
프로세스는 사용하지 않는다. 이 결과를 Windows OS 호출이 실제로 실패한 실측으로 간주하지 않는다.

| 주입 | 보완 뒤 동작 | 보장하는 계약 |
|---|---|---|
| terminate 예외 | kill까지 독립적으로 시도해 종료가 확인되면 핸들을 회수한다. | 원래 고정 오류 보존, 부모 IPC 정리 |
| join 예외 | 실제 종료 여부를 다시 확인해 종료된 핸들을 회수한다. | 한 단계 실패가 나머지 정리를 막지 않음 |
| kill 예외 | 살아 있는 worker 참조와 격리 상태를 유지한다. | 미종료 자원 추적, 추가 spawn 차단 |
| kill 이후에도 생존 | `worker_stop_failed`를 남기고 참조를 유지하며 이후 요청을 거부한다. | 무제한 종료 재시도 없이 소유권 보존 |

`_stop()`은 부모 IPC를 먼저 분리해 마지막에 독립적으로 닫고, 종료 API 예외를 호출자에게
전파하지 않는다. 종료가 확인된 프로세스만 닫아 참조를 해제한다. 살아 있거나 상태 확인에
실패한 프로세스는 `_stop_unconfirmed` 상태로 보존한다. 이후 요청은 자연 종료 여부만 다시
확인하며 같은 종료 API를 반복 호출하지 않고, 미종료 상태에서는 새 spawn을 시작하지 않는다.

MCP의 `to_tool_error`는 이 OSError를 본문 없는 `INTERNAL_ERROR`로 변환한다.
따라서 **MCP 응답에 원본 예외가 노출된 것으로 보고하지 않는다.** 변환기 수준에서 합성
민감 문자열 미포함, stdout 미출력, 직렬화 잠금 해제를 별도로 검증한다. 실제 MCP 프로토콜
왕복이나 답변 생성 전체 흐름을 이 테스트로 검증한 것은 아니다.

## 테스트 해석

정상 종료·오류 변환 방어와 #255에서 추가한 오류 보존·IPC 정리·소유권·추가 spawn 차단은
모두 일반 회귀 테스트다. 종료 단계가 실패해도 최종 종료가 확인된 경우에는 핸들을 회수하고,
실제로 생존한 경우에만 소유권과 격리 상태를 유지하는 불변식을 검증한다.

```powershell
python -m pytest tests/unit/test_worker_shutdown_contract.py -q -rx
python -m pytest tests/unit/test_process_embedder.py tests/integration/test_windows_log_lifecycle.py tests/integration/test_windows_mcp_log_bundle.py -q -rs
```

Windows 11 / Python 3.12에서 종료 실패 계약 파일은 **18 passed**, 기존 ProcessEmbedder와
Windows worker 수명 테스트는 **27 passed**다. 이는 대역에 의한 종료 API 실패 검증이며 실제
Windows 종료 호출 실패나 wheel 설치본의 신규 실측 결과는 아니다.

## 완료 범위와 남은 #99 범위

1. **A 완료 — 오류·정리·소유권:** 고정 요청 오류, 부모 IPC 정리, 종료 확인 뒤 핸들 회수,
   미종료 worker 소유권과 추가 spawn 차단을 구현했다. 원본 정리 예외는 재로깅하지 않는다.
2. **남은 로그 생산자 연결:** 새 묶음 규약의 잠금/정상 종료 기록 연결은 별도 구현이다.
   모든 writer 종료를 확인하지 못한 상태에서는 `.closed.json`을 게시하지 않는다. 현재 구형 worker
   로그의 unknown 보존과 삭제 비활성 정책은 유지한다.
3. **C 완료 — 실제 Desktop:** Windows 앱 정상 종료·강제 종료·재시작에서 잠금, 종료 기록,
   이전 unknown 보존을 확인했다. 상세 결과는 #99 최신 코멘트를 따른다.
4. **남은 B/C 범위:** 로그 순환·보관 기간·명시적 정리와 전체 용량 정책을 구현하고 완료 조건을
   다시 점검한다.

실제 API 호출, Desktop 재설치, 기존 로그 정리는 수행하지 않는다. 로그 생산자 연결과 순환·정리가
남아 있으므로 #99는 열린 상태로 유지한다.
