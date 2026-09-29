# MCP 임베딩 프로세스 분리

MCP 시작 시 sentence_transformers를 import하지 않는다. 검색 및 MCP 로컬 PDF 등록은
같은 ProcessEmbedder를 사용하고 첫 임베딩 요청에 Windows spawn 전용 프로세스를 시작한다.
라이브러리 import·모델 로딩·계산은 자식의 메인 스레드에서 수행한다.
정상 프로세스는 재사용하며 모델이 바뀌면 이전 모델 캐시 참조를 비운다.
모델 ID는 등록 설정 및 기존 색인에 저장된 ID를 사용하며 임의 모델로 대체하지 않는다.

## 시간 제한과 오류

`EMBEDDING_PROCESS_TIMEOUT_SECONDS` 기본 90초. 대기열·프로세스 시작·IPC·로딩·계산을
합친 요청별 제한이다. 프로세스 종료 정리에 추가로 수 초가 걸릴 수 있다.
작업 대기 중 제한 초과는 다른 요청의 프로세스를 죽이지 않는다. 자신이 처리 중인 요청의
제한 초과·잘못된 응답·통신 오류는 프로세스를 폐기한다. 자동 재시도하지 않으며
다음 요청에서 새로 생성한다. 큰 IPC 송수신도 별도 스레드에서 수행해 시간 제한을 적용한다.

질문 도구는 UPSTREAM_UNAVAILABLE과 EMBEDDING_TIMEOUT / EMBEDDING_UNAVAILABLE /
EMBEDDING_INVALID_RESULT / EMBEDDING_CLOSED 코드를 반환한다. 근거 부족으로 숨기지 않는다.
등록 실패는 작업을 failed로 바꾸고 limitations에 안전한 임베딩 오류 코드를 기록한다.
반환 벡터 수·차원·유한성을 검사하고, 검색 어댑터는 저장 색인의 차원과 다시 비교한다.

## 종료·통신·진단

임베딩 프로세스는 사설 multiprocessing Pipe로 통신한다. 질문·번역문은 계산을 위해
전달하지만 IPC 내용을 로그에 기록하지 않는다. 라이브러리 stdout은 자식 stderr로 보낸다.
부모 요청 ID는 자식 단계 로그에도 전달된다. 부모에 embedding_process_wait 및
embedding_process_response 단계가 추가된다. 외부 라이브러리 stderr에는 별도 정보가
들어갈 수 있으므로 전체 로그 공유는 피한다.
자식은 전용 로그 파일을 직접 열고 진입 및 요청 수신·결과 전송을 기록한다.
모델 import 전부터 스택 타이머를 예약하고, 실제 작업 루프에서는 요청마다 재예약한다.
30초(요청 제한이 짧으면 그 절반) 후 모든 Python 스레드 스택을 한 번 기록한다.
정상 완료 시 취소하므로 유휴 상태에서는 덤프하지 않는다.
정상 MCP 수명 종료 시 프로세스를 닫고 등록 실행기의 대기 작업을 취소한다.
OS 강제 종료 시 정리 완료까지 보장하는 작업 객체(Job Object) 구현은 포함하지 않는다.

## 적용

Claude args에서 `--preload-embedding-library`를 제거하고 로그 래퍼 경로만 남긴다.
과거 선행 import 옵션은 비교 진단용이며 새 운영 경로에서는 사용하지 않는다.
모델 캐시가 없으면 90초를 넘길 수 있으므로 먼저 prepare_embedding_model.py로 준비한다.
모델이 준비돼도 별도 프로세스에서의 로딩 시간은 필요하다.

일반 Python 함수·run_paper.py의 기본 임베더는 기존 직접 실행 경로를 유지한다.
이번 변경은 정식 MCP 런타임의 검색 및 로컬 PDF 등록에 적용된다. URL 직접 등록은
현재 MCP 도구에서 노출하지 않으며 별도 함수 호출까지 자동 변경하지 않는다.
이 조치는 지연을 격리할 뿐 SciPy 초기화 지연의 근본 원인을 해결한다고 보장하지 않는다.

## 자식 프로세스 로그 확인

MCP 데이터 루트의 diagnostics 폴더에 `worker-작업자ID.log`와
`worker-stack-작업자ID.log`가 생성된다. 기본 설정은 `runtime/diagnostics`다.
부모 로그의 worker_spawn_start / worker_spawn_return / worker_stopped에 같은 worker_id가
들어간다. 종료 기록에는 PID·종료 코드·생존 여부만 포함하며 원문·질문·키는 포함하지 않는다.
자식 진입 직후 worker_enter를 기록하며 이후 단계 로그는 요청 ID로 연결한다.

Windows 자식은 부모 표준 핸들을 물려받아 부팅 중 검사한다. MCP stdio 서버처럼 부모가
stdin 파이프를 블로킹 읽기 중이면 자식이 worker_enter 전에 멈춰 EMBEDDING_TIMEOUT이 난다.
그래서 프로세스 생성 동안만 STD_INPUT_HANDLE을 NUL로 바꾼다. 부모의 MCP stdin 읽기는
CRT fd 0 핸들을 쓰므로 영향이 없고, 이는 회귀 테스트로 확인한다.

부모의 spawn 반환은 OS 프로세스 생성이지 Python 부팅 완료를 뜻하지 않는다.
worker_enter가 없다면 자식 부팅 이전 지연 또는 로그 파일 준비 실패가 후보이며
임베딩 import 문제로 단정하지 않는다. 스택 타이머는 자식 진입 이후에만 작동한다.
외부 라이브러리 출력과 스택에는 경로 등이 포함될 수 있으므로 전체 파일 공유에 주의한다.
로그는 덮어쓰거나 자동 삭제하지 않는다. 빈 스택 파일은 시간 제한 전에 끝났을 수 있다.
