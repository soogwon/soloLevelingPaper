# #99 Windows 로그 수명 검증

2026-11-08, main `09b98f8`, Windows 11(10.0.26300), Python 3.12.14,
로컬 D: NTFS 기준이다. 이전 primitive 검증(#189) 이후 main에 들어온 실제 Windows
어댑터·MCP 생산자 연결을 재검증했다. 제품 코드·보관 정책·삭제 기능은 변경하지 않았다.

## 검증 결과와 의미

| 대상 | 확인 내용 |
|---|---|
| 실제 MCP 기본·상세 로그 | stdio 초기화 응답 수신, 실행 중 inspector의 busy, 정상 종료 뒤 잠금 해제·정상 종료 기록과 실제 파일 ID·크기 일치 |
| 강제 종료 후 재시작 | 비정상 종료 코드 확인, 이전 실행의 정상 종료 기록 없음, 기존 로그 파일 ID·바이트 보존·목록의 unknown 유지. 새 실행이 busy인 동안 이전 실행의 잠금은 획득 가능 |
| 지연된 자원 정리 | 지연 작업 완료·stderr 및 fd 복원·출력 스트림 닫기 뒤에만 정상 종료 기록 게시 |
| 실패 주입 | busy·게시 실패·fsync 실패·디렉터리 생성 실패·release 오류에서도 원래 합성 stdout 유지, 민감한 합성 본문 우회 출력 없음 |
| 실제 합성 worker | 정상 반환·예외·시간 초과를 기본/원본 수집 두 모드로 실행. 별도 Windows 프로세스 핸들의 대기 결과로 실제 종료를 확인하고, Process 객체와 IPC 연결의 닫힘을 검사 |
| worker 출력 핸들 | 종료 뒤 worker 로그·스택 파일을 Windows 공유 없는 읽기 핸들로 열 수 있고, 이후 바이트 불변 |
| worker watchdog | 정상 반환·예외 경로에서 실제 cancel 호출이 스택 스트림 닫기 전에 발생. 시간 초과에서는 스택 덤프 후 Windows 강제 종료·핸들 해제 확인 |
| 기존 worker 로그 | 새 규약 정상 종료 기록을 만들지 않고 목록의 activity=unknown 유지. 목록 조회 후 바이트 보존 |

강제 종료된 자식의 Python `finally` 실행이나 watchdog의 정상 취소는 보장하지 않는다.
해당 경로는 프로세스 종료와 파일 핸들 해제로 판정한다. 종료된 worker가 늦게 파일을 쓰지
않는지 단시간 재확인하지만, 그 관찰만으로 모든 종류의 다른 writer 부재를 증명하지는 않는다.

원본 로그 모드에는 합성 마커만 기록했다. 기본·상세 안전 로그와 부모 stdout/stderr에는
그 마커가 나타나지 않는지 검사한다. 실제 질문·논문·키·모델·외부 API는 사용하지 않았다.

## 변경한 테스트

- `test_windows_log_lifecycle.py`: POSIX 종료 순서·실패 probe를 Windows 어댑터에 적용한
  7개와 실제 spawn worker 6개, 종료 생략을 탐지하는 음성 대조군 1개다.
  자식이 지정한 소스/설치 경로에서 제품 모듈을 읽었는지도 확인한다.
- `test_windows_mcp_log_bundle.py`: 기존 실제 MCP 강제 종료·재시작 테스트에 이전 실행의
  unknown 유지·종료 기록 부재 검사를 추가했다. 테스트 전용 설치 경로 선택을 지원한다.
- 현재 제품의 공용 종료·보관 정책은 바꾸지 않았다. 원본 로그를 일반 안전 로그로 승격하거나
  종료됐다는 이유만으로 삭제 가능하다고 판정하지 않는다.

재검토에서는 종료 이벤트·`_process is None`만으로 종료를 증명하지 않도록 보강했다.
생성 직후 별도 OS 핸들로 해당 프로세스를 고정하고 모든 종료 경로에서
`WaitForSingleObject(handle, 0)`의 종료 상태를 확인한다. 종료를 생략하고 참조만 지우는
대조군에서는 `WORKER_STILL_ALIVE`가 발생해야 통과한다. 대조군도 `finally`에서 자신이
생성한 프로세스만 같은 핸들로 종료·회수해 남기지 않는다.

MCP stdio 자식도 실제로 지정한 wheel/소스 경로에서 로딩되는지 확인하며, 개발자의
env 파일 선택·제공자 설정을 제거한 합성 환경에서 실행한다.

## 재실행

```powershell
python -m pytest tests/integration/test_windows_log_lifecycle.py tests/integration/test_windows_mcp_log_bundle.py tests/integration/test_windows_log_retention.py -q -rs
```

추가 회귀 대상은 `test_embedding_log_privacy.py`, `test_process_embedder.py`,
`test_log_retention_policy.py`, `test_log_inventory.py`, `test_safe_diagnostic_stream.py`,
`test_mcp_diagnostic_runner.py`, `test_diagnostics.py`다. 임시 폴더만 사용하고 필요하면
`--basetemp`에 기존 사용자 데이터와 겹치지 않는 새 절대 경로를 지정한다.

## wheel 설치본

새 worktree에서 `python -m build --wheel --no-isolation`으로 빌드하고, 새 임시 target에
`pip install --no-index --no-deps --target <새 설치 폴더> <wheel>`로 설치했다.
테스트 전용 환경 변수 `SOLO_LOG_TEST_INSTALLED_ROOT`를 해당 폴더로 지정하고
`test_windows_log_lifecycle.py`·`test_windows_mcp_log_bundle.py`를 반복했다.
초기 **17 passed**, 재검토 보강 후 **18 passed**였다.
초기 source의 새 수명 테스트만 단독 실행한 결과는 **13 passed**이며,
보강 후 MCP 테스트와 함께 재실행한 결과는 **18 passed**다.

전체 source 회귀는 초기 **303 passed, 1 skipped**, 재검토 보강 후
**304 passed, 1 skipped**다. 건너뛴 테스트는 현재 사용자에게
심볼릭 링크 생성 권한이 없어 실행하지 못한 기존 inventory 테스트 1개다.
junction 검증 통과를 symlink 검증 통과로 대신 기록하지 않는다.

wheel SHA-256:
`1606469ce4c5b86bffff87db4d962539b1b3a97f087663d1a5986ea817897290`

이 설치는 프로젝트 패키지만 별도 target에 넣고 테스트 Python의 기존 의존성을 재사용했다.
독립 가상환경 전체 설치나 실제 Claude Desktop 검증은 아니다. 사용자 Desktop 설치본·설정은
변경하지 않았다. wheel·설치본·테스트 DB·원본 수집 합성 로그는 runtime의 로컬 산출물이며 커밋하지 않는다.

## 남은 A·C 확인

- **A:** worker 로그는 현재 새 묶음 규약에 연결되지 않았다. 작업 소유권·모든 writer 종료·
  스택 타이머·출력 핸들 정리와 부모의 종료 확인을 연결한 뒤에만 정상 종료 기록 게시를 검토한다.
  이번 검증은 기존 unknown 보존 정책을 유지한다.
- **C:** 실제 Windows Desktop의 앱 종료·강제 종료·재시작에서도 동일한 잠금/종료 기록
  결과가 나오는지 확인한다. 자동 검증은 실제 MCP stdio와 합성 지연 작업까지다.
- 실제 프로세스 종료 API 자체의 실패, 백신 간섭, 전원 장애, 다른 파일시스템은 검증하지 않았다.
- 로그 자동 순환·삭제 안전성 완료를 뜻하지 않는다. #99는 전체 완료로 닫지 않는다.
