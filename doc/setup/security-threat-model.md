# 로컬 MCP 보안 위협 모델과 검증 범위 (#64)

작성일: 2026-10-22

## 범위와 전제

신뢰할 수 있는 사용자가 로컬에서 실행하는 stdio MCP 서비스가 대상이다.
원격 다중 사용자 서비스의 인증·권한 분리를 보장하는 문서가 아니다.
데이터 폴더와 설정 파일은 해당 사용자가 관리하며, 신뢰하지 않는 사용자에게
쓰기 권한을 부여하지 않는 것을 전제로 한다.

보호 대상은 논문 원문·번역·질문·근거, API 키, 허용 폴더 밖의 파일과 진단 로그다.
사용자 입력 경로, PDF 내용, 모델 응답과 라이브러리 출력은 신뢰하지 않는다.
MCP 호스트에 반환하는 정상 근거·본문과 진단 로그는 구분한다.
호스트에 전달한 결과의 후속 처리는 이 서버의 설정만으로 통제할 수 없다.

## 위험과 방어·검증 연결

| 위험 / 경계 | 현재 방어 | 검증 파일 | 남은 제한 |
|---|---|---|---|
| 입력 경로 → 로컬 파일: 허용 폴더 밖 접근 | 절대·상위·Windows 특수 경로와 루트 밖 심볼릭 링크 거부 | `tests/unit/mcp/test_local_files.py`, `tests/unit/mcp/test_runtime.py` | 검사 후 경로 교체·하드링크 원본 위치는 보장하지 않음 |
| 입력 PDF → 관리 저장소: 위장·과대 파일과 덮어쓰기 | 확장자·헤더·스트림 크기 검사, 해시 기반 저장·기존 내용 확인 | `tests/unit/mcp/test_local_files.py`, `tests/unit/test_pdf_store.py` | 헤더 검사는 악성 PDF 검사나 파서 격리가 아님 |
| 설정 → 번역·생성 제공자: 의도하지 않은 전송 | 외부 호출 허용이 참이고 로컬 전용이 거짓일 때만 호출 | `tests/unit/mcp/test_external_api_policy.py`, `tests/unit/mcp/test_runtime.py`, `tests/unit/translation/test_openai_provider.py` | 호스트 통신·모델 다운로드까지 차단하는 설정이 아님 |
| 제공자·내부 예외 → MCP 응답: 키·경로 노출 | 원본 예외 대신 고정 오류 응답으로 변환 | `tests/unit/mcp/test_server.py`, `tests/unit/translation/test_openai_provider.py`, `tests/unit/test_openai_generator.py` | 정상 도구 결과에는 요청한 원문 근거가 포함될 수 있음 |
| 부모 stderr → 진단 파일: 본문·키·개인정보 기록 | 진단 실행기는 기본적으로 허용된 단계 로그만 저장, 네이티브 원본 출력 폐기 | `tests/unit/test_safe_diagnostic_stream.py`, `tests/unit/test_mcp_diagnostic_runner.py` | 일반 MCP 직접 실행에는 같은 필터가 적용되지 않음 |
| 임베딩 자식 출력 → 로그 파일: 원본·스택 노출 | IPC 예외 본문 제외, 원본 로그 보호는 #85 후속 | `tests/unit/test_process_embedder.py`는 기존 동작 회귀 검증 | 부모 필터만으로 자식 로그를 보호하지 못함 |

## 실행 경로별 로그 보호

- `interfaces.cli.desktop serve`와 `interfaces.cli.mcp_diagnostics`는 기본 원본
  비수집을 적용한다. `--collect-raw-logs`는 민감정보가 포함될 수 있는 상세 수집을
  명시적으로 허용하는 옵션이다. 원본 로그를 그대로 공유하지 않는다.
- `python -m solo_leveling.interfaces.mcp`는 위 진단 실행기를 거치지 않는다.
  기본 보호 수준의 통일은 #85 설정 전달 계약과 함께 검토한다.
- 현재 임베딩 작업 프로세스의 원본 출력·스택 수집은 별개다. A 담당 구현과 C의
  실행 설정 전달·통합 검증이 필요하며, 이번 문서·테스트 보완으로 해결되지 않는다.
- 안전 필터는 정확한 필드 집합과 단계·오류 코드·ID 형식·수치 범위만 허용한다.
  허용되지 않은 정상 진단 이벤트도 폐기될 수 있으므로 새 이벤트 추가 시 검토한다.
  ID 형식 검사는 악의적인 코드의 정보 은닉이나 출력의 진위를 증명하지 않는다.
- 상세 수집을 꺼도 기존 로그가 삭제되는 것은 아니다. 접근 권한·보존·정리 정책은
  별도로 결정해야 한다.

## 이번 보완 테스트의 의미

- 번역 차단 설정 세 조합에서 HTTP 요청이 발생하지 않는지 확인한다.
- 설정 파일보다 프로세스 환경이 우선하고, 네 조합 모두 실제 서비스 조립을 거쳐
  번역·생성 제공자에 전달되는지 확인한다. 저장소·작업 실행은 대역으로 교체한다.
- 잘못된 JSON·객체가 아닌 입력·필드 누락·타입·범위 오류를 폐기하며,
  허용된 시작·종료·실패 이벤트와 다음 정상 레코드는 보존하는지 확인한다.
- 합성 키·텍스트와 테스트 대역만 사용한다. 실제 API 호출, 모델 다운로드,
  사용자 DB·PDF를 사용하는 검증이 아니다.

테스트 통과는 이 입력 범위의 회귀 검증이지 모든 공격에 대한 안전성 증명이 아니다.
실제 Mac/Linux와 Claude 연결 검증은 별도로 기록한다.

### 실행 결과 (2026-10-22)

Windows / Python 3.12 환경에서 아래 범위를 실행하여 **352개 통과, 2개 건너뜀**을
확인했다. 건너뛴 항목은 심볼릭 링크 생성 권한이 필요한 파일·디렉터리 링크 테스트다.
최초 실행은 기본 임시 디렉터리 접근 권한 문제로 준비 단계에서 실패했으며,
별도 임시 경로와 필요한 실행 권한으로 재실행했다.

```text
python -m pytest -q -p no:cacheprovider --basetemp=<새로운 테스트 전용 임시 경로>
  tests/unit/mcp
  tests/unit/test_safe_diagnostic_stream.py
  tests/unit/test_mcp_diagnostic_runner.py
  tests/unit/translation/test_openai_provider.py
  tests/unit/test_openai_generator.py
  tests/unit/test_pdf_store.py
  tests/integration/test_mcp_stdio.py
  tests/integration/test_mcp_workflow.py
```

위 목록은 한 명령으로 연결해 실행한다. `--basetemp`에는 기존 데이터가 없는
테스트 전용 경로를 지정한다. 전체 테스트 모음·실제 API·Claude·Mac/Linux를
검증한 결과는 아니다.

## 완료 전에 남은 확인

- [ ] #85의 A 구현과 C 설정 전달 연결
- [ ] 기본 모드에서 부모·자식 모두 합성 민감정보가 기록되지 않는지 확인
- [ ] 상세 모드 허용 및 해제 후 재시작 동작 확인
- [ ] Desktop·일반 MCP의 정상·실패·시간 초과·종료 통합 검증
- [ ] 검증 환경·결과·남은 제한을 바탕으로 #64 완료 여부 판단

검사 후 경로 교체 방어는 이번 범위에서 보류한다. 신뢰할 수 있는 로컬 폴더라는
전제가 달라지면 별도 설계가 필요하다. PDF·모델 출력의 지시문에 의한 프롬프트 주입,
악성 PDF 파싱, 원격 인증·인가도 현재 경로 검사나 로그 필터만으로 해결되지 않는다.

관련 안내: [외부 전송 데이터와 시점](external-data-transfer.md),
[MCP 실행·파일 제한](mcp-server.md),
[임베딩 로그 협의](embedding-log-privacy-coordination.md).
협의 문서의 과거 진행 상태와 별개로 실제 #85 구현·검증 완료를 확인해야 한다.
