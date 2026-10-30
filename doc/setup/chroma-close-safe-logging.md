# #120 C 담당: Chroma 종료 실패 안전 로그

기준: main `cc48503`, 작업 브랜치 `fix/issue-120-chroma-close-log`.
관련: [#120](https://github.com/soogwon/soloLevelingPaper/issues/120),
[#121](https://github.com/soogwon/soloLevelingPaper/pull/121),
[#91](https://github.com/soogwon/soloLevelingPaper/issues/91).

## 변경 범위

수집이 직접 소유한 클라이언트를 정리할 때 출력하는 `chroma_close` 이벤트가
Desktop·진단 실행기의 기본 안전 필터에서 사라지는 문제를 수정했다.
A의 `workers/ingestion.py`, `infrastructure/storage/vector_store.py`는 변경하지 않았다.

허용 형식은 `event`, `code` 두 필드로 한정한다.
`event`는 `chroma_close`, `code`는 `CHROMA_CLOSE_FAILED` 또는
`CHROMA_CLOSE_UNSUPPORTED`인 문자열만 허용한다. 기본·상세 안전 모드 모두 적용한다.
추가 필드, 임의 문자열, 성공 코드, 잘못된 타입은 폐기한다.
이벤트 보존은 종료 성공을 의미하지 않으며 원본 로그 수집을 켤 필요가 없다.

## 검증

macOS arm64 / Python 3.12.14 / ChromaDB 1.5.9에서 다음을 검증한다.
로그 쓰기 실패 후 복구 보완을 포함하여 관련 테스트 **170개가 모두 통과**했다.

- 분할 출력 보존, 기본·상세 모드, 허용 코드와 타입·필드 거부
- 로그 쓰기 실패가 소유자의 정리 경로 밖으로 전파되지 않음
- 쓰기 실패 시에도 내부 버퍼를 초기화하여 다음 종료 로그와 일반 단계 로그가 정상 저장됨
- 실제 임시 SQLite·Chroma 수집 성공/실패에 합성 종료 결과를 주입하여 원래 결과·예외 보존
- Desktop `serve` 및 진단 실행기를 별도 프로세스로 실행하고 실제 MCP 초기화·도구 목록·호출 확인
- 실패·미지원 코드만 진단 파일에 남고 추가 경로 필드·원본 예외는 남지 않음

번역·임베딩과 MCP 업무 서비스는 테스트 대역을 사용한다.
사용자 DB·논문·API 키 및 외부 API는 사용하지 않는다.
Claude Desktop 화면 검증이나 Linux·Windows 실행을 완료했다는 의미는 아니다.

실행 명령:

```sh
python -m pytest tests/integration/test_chroma_close_safe_logging.py tests/integration/test_ingestion_chroma_ownership.py tests/integration/test_mcp_stdio.py tests/unit/test_safe_diagnostic_stream.py tests/unit/test_mcp_diagnostic_runner.py -q
```

## 남은 범위

A 담당인 실제 Chroma 종료 실패 후 재호출 성공 오표시와 지원 버전 정책은 그대로 남아 있다.
이번 수정만으로 #120 또는 #91을 종료하지 않는다.
#121의 `runtime_cleanup`과 이번 `chroma_close`는 서로 다른 이벤트이므로,
두 PR 통합 시 두 허용 계약을 모두 보존해야 한다.
