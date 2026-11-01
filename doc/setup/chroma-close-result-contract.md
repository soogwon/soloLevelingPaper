# Chroma 종료 결과 계약 (#120, #91 후속)

`close_client(client)`의 결과 코드와 의미. 호출자(MCP 종료, 수집 워커)는 이 코드로 성공 여부를 판단한다.

| 결과 | 의미 | 성공으로 보는가 |
|---|---|---|
| `CHROMA_CLOSED` | 이번 호출에서 `close()`가 예외 없이 끝났다 | 예 |
| `CHROMA_ALREADY_CLOSED` | 이 함수가 이미 닫은 클라이언트, 또는 `None` | 예 |
| `CHROMA_CLOSE_UNSUPPORTED` | `close()`가 없는 클라이언트 | 아니오 |
| `CHROMA_CLOSE_FAILED` | `close()`가 예외를 냈다. **같은 객체는 다시 호출해도 계속 FAILED** | 아니오 |

## 왜 실패는 다시 호출해도 FAILED인가
Chroma 1.5.9의 `Client.close()`는 닫힘 표시(`_closed = True`)를 먼저 하고 그 뒤에 공유 시스템의 참조를 해제한다.
해제 도중 예외가 나면 표시는 이미 켜져 있으므로, 이후의 `close()`는 아무것도 하지 않고 정상 반환한다. 이 반환을
성공으로 보면 시스템이 남아 있는데도 `CLOSED`로 기록된다(Windows에서 재현: 첫 호출 FAILED, 둘째 호출 CLOSED, 시스템 잔존).

그래서 실패한 클라이언트는 FAILED로 유지하고 `close()`를 다시 부르지 않는다. 복구 수단으로 reset, 전역 캐시 삭제,
강제 `system.stop`은 쓰지 않는다(같은 경로를 공유하는 다른 클라이언트에 영향이 간다). 복구는 새 클라이언트를 만들거나
프로세스를 끝내는 것이다.

## 한계
- 속성을 붙일 수 없는 객체(`__slots__` 등)는 실패·성공 표시를 기억하지 못한다. Chroma 클라이언트는 해당 없다.
- 직접 `client.close()`를 호출한 쪽이 실패를 삼킨 경우는 이 함수가 알 수 없다. 종료는 `close_client`로만 한다.

## 지원 Chroma 버전
- 검증한 버전은 **1.5.9**다(macOS arm64, Ubuntu 24.04, Windows AMD64). 이것은 하한을 확정한 것이 아니다.
- 하한은 다른 버전에서 같은 테스트(`tests/unit/test_vector_store_close.py`, `tests/integration/test_ingestion_chroma_ownership.py`)를
  돌려 본 뒤 A와 C가 함께 정한다. `pyproject`의 버전 제한은 이 변경에서 바꾸지 않는다.
