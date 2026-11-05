# #99 Windows 로그 보관 기반 검증

2026-11-05, main `65882b5` 기준. Windows 11 (10.0.26300), Python 3.12.14,
D: NTFS에서 임시 파일과 합성 자식 프로세스만 사용했다. 제품 어댑터, MCP/worker
생산자 연결, 순환·삭제 기능은 추가하지 않았다. 실제 로그·논문·DB·API는 사용하지 않았다.

## 결과

| 검증 | 확인한 결과 |
|---|---|
| 비차단 배타 잠금 | `LockFileEx(EXCLUSIVE_LOCK | FAIL_IMMEDIATELY)`로 1바이트를 잠그면 다른 프로세스는 busy, 핸들 종료 후 acquired |
| inspector | `OPEN_EXISTING`은 없는 잠금 파일을 생성하지 않음 |
| 잠금 파일 교체 | `CreateFileW`에서 삭제 공유를 허용하지 않으면 잠금 보유 중 삭제·교체가 거부됨. 파일 ID와 자식의 busy 판정 유지 |
| 자식 미상속 | `HANDLE_FLAG_INHERIT`가 꺼져 있음. `close_fds=False`로 시작한 자식이 살아 있어도 부모 핸들 종료 후 새 프로세스가 잠금 획득 |
| 파일 동일성 | 재개방한 파일의 `(st_dev, st_ino)`가 같고 같은 경로를 다른 파일로 교체하면 달라짐 |
| 하드 링크 | `st_nlink=2`로 식별. 하나보다 큰 링크 수는 어댑터에서 제외해야 함 |
| 재분석 지점 | 임시 directory junction의 `lstat().st_file_attributes`에서 REPARSE_POINT 확인 |
| 무덮어쓰기 게시 | 같은 폴더 임시 파일에 write/flush/fsync 후 `os.link`로 게시. 100회 반복에서 동시 reader가 게시 파일을 실제 읽었고 부분 내용 0건, 기존 이름 게시 거부, 기존 내용 보존 |
| 경쟁 게시 | 서로 다른 내용의 두 임시 파일을 두 스레드에서 같은 이름으로 게시하면 하나만 성공하고 최종 내용·파일 ID가 승자와 일치 |
| 테스트 종료 정리 | 응답하지 않는 합성 자식은 제한 시간 후 종료하고 회수하며 파이프를 닫음 |

파일 삭제·교체 거부는 연산에 따라 WinError 5(접근 거부) 또는 32(공유 위반)로
나온다. 오류 코드 하나만 전제하지 않고 파일 동일성·잠금 보존도 함께 검사한다.
게시 후 임시 이름을 제거하면 최종 파일의 링크 수는 1이다. 제거 전/중단 시 링크 수가
2인 파일은 정상 종료·정리 가능 상태로 승격하면 안 된다.

## 재실행

저장소 루트에서 Windows Python으로 실행한다. 제품 데이터 경로를 지정하지 않는다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m pytest tests/integration/test_windows_log_retention.py tests/unit/test_log_retention_policy.py tests/unit/test_log_inventory.py tests/unit/test_safe_diagnostic_stream.py tests/unit/test_mcp_diagnostic_runner.py -q -rs
```

재검토 후 결과: **246 passed, 1 skipped**. 신규 Windows 검증 9건은 모두 통과했다.
건너뛴 기존 미리보기 테스트는 심볼릭 링크 생성 권한 부족 때문이다.
junction 검증이 symlink 검증을 대신하지는 않는다. 비Windows에서는 신규 테스트를 건너뛴다.
안전 로그·미리보기·MCP 진단 실행기 회귀는 기존 기능에 대한 검증이며,
아직 존재하지 않는 OS 어댑터의 실패 처리를 검증한 것은 아니다.

## 어댑터 구현에 넘길 제안과 제한

- Windows 어댑터 후보: 미상속 핸들 + 삭제 공유 없는 `CreateFileW` + 비차단
  `LockFileEx`. 잠금 파일은 종료 후에도 유지한다. inspector는 생성하지 않는다.
- 파일 동일성 후보는 `(st_dev, st_ino, st_size)`다. 이번 결과는 로컬 NTFS에만
  해당하며 네트워크 드라이브·다른 파일시스템까지 승인한 것이 아니다.
- `FILE_FLAG_OPEN_REPARSE_POINT`는 마지막 경로 요소에 대한 옵션이다. 상위 폴더
  junction이나 루트 교체까지 막았다고 해석하면 안 된다. 실제 어댑터는 루트·상위 경로와
  열린 핸들의 동일성을 확인해야 한다. 이번 테스트는 재분석 지점 식별만 확인했다.
- 파일 ID·크기 검사는 동시 쓰기나 같은 크기의 내용 수정을 감지하는 완전한 증명이 아니다.
  모든 writer 종료와 핸들 정리 후 게시한다는 기존 계약이 반드시 필요하다.
- `os.link` 게시 테스트는 동시 reader에 대한 완전한 내용 노출과 덮어쓰기 거부를
  확인한다. 두 게시자 경쟁은 같은 프로세스의 서로 다른 스레드로 검사했으며,
  서로 다른 프로세스의 게시 경쟁은 아직 검사하지 않았다.
  전원 장애 내구성, 디스크 부족, 백신 간섭은 검증하지 않았다.
- 핸들 미상속이 worker의 별도 로그 핸들·네이티브 출력·스택 타이머 종료를 보장하지 않는다.
  자식 종료 대기, 모든 출력 핸들 정리, 종료 게시 순서는 생산자 연결 때 검증한다.

A·C와 위 Windows 선택 및 identity 직렬화 형식을 확정한 뒤 공통 Protocol의
실제 OS 어댑터를 구현한다. 잠금 실패 시 새 파일 로그 생성 생략, 게시 실패 시 unknown
유지, 원래 요청 결과 보존, 원문 우회 출력 금지라는 합의는 유지한다.
그 다음에 생산자 연결과 실패 주입 테스트를 진행한다. 이번 검증만으로 삭제를 활성화하거나
#99를 완료 처리하지 않는다.
