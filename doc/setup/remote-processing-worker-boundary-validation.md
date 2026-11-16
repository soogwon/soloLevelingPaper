# #517 병합 후 worker·B 처리 경계 합성 검증

실행일: 2026-11-17 새벽 예약 작업(KST). 기준 main: `b7595d0`.
브랜치: `soogwon/fix/issue-517-worker-boundaries`.
#531·#534는 병합됐으며, A의 R2 입력/worker #532와 heartbeat #535도 포함된 상태에서 대조했다.

## 보완한 B 결함

지원 Python 하한은 3.11이지만 `Path.is_junction`은 그 환경에서 사용할 수 없다.
기존 adapter는 이 메서드가 없으면 False를 기본으로 사용해 Windows reparse 디렉터리를
거부하지 못했다. 메서드를 제거하고 `lstat.st_file_attributes`에 reparse 속성을 공급한
합성 재현에서 예상 오류가 발생하지 않고 모델 factory가 호출되는 것을 확인했다.

- 디렉터리 symlink·사용 가능한 is_junction 검사에 더해 Windows lstat의
  `FILE_ATTRIBUTE_REPARSE_POINT`(0x400)를 확인한다. 해당 디렉터리는 모델 로딩 전에 거부한다.
- 일반 디렉터리의 파일 선택·내용 SHA·정렬 JSON 규칙은 바꾸지 않았다. 메서드가 없는 환경의
  정상 snapshot도 기존 해시와 같고 정상 결과를 반환하는 대조 회귀를 추가했다.
- HF blob 파일 링크 허용 정책은 그대로다. 디렉터리 밖의 loader 입력을 해시의 빈틈으로 허용하지 않는다.

이 테스트는 Windows/Python 3.12에서 메서드 부재와 OS 속성을 대역으로 재현한다.
실제 Python 3.11 interpreter나 실제 OS junction 생성·교체 경쟁을 검증한 것은 아니다.

## 병합된 구성 요소의 합성 조립

새 `test_processing_worker_boundaries.py`는 실제 RemoteProcessingWorker·heartbeat thread,
RemoteProcessingStages·PDF 추출기/청커·번역 서비스/gate·PyTorch adapter·분류 helper를
함께 조립한다. 큐·입력 소유권 조회·R2 reader·전송/사용량 원장·HTTP·모델 factory는 합성 대역이다.
원문은 기존 합성 PDF fixture이며 모델 파일도 임시 합성 bytes다. 네트워크·모델 import를 차단한다.

| 사례 | 확인 결과 |
|---|---|
| 정상 처리 | worker가 동일 job/lease의 prepared를 반환하지만 job은 running. 분류는 await_publication이며 성공/게시/검색 가능을 생성하지 않음 |
| HTTP timeout·503·usage 누락 | 원장의 unknown 1건·물리 post 1회. usage_pending, retryable=null. 모델 factory/encode 미호출 |
| 부분 번역 | translation_incomplete·embedding 없음, 미게시 중단 힌트. 모델 로딩 없음 |
| 입력 검사 거부 | provider 원장 요청·HTTP·모델 로딩 없음 |
| adapter 해시 계산 중 입력 거부 | 로딩 직전 재검사에서 중단, factory/encode 미호출 |
| heartbeat lease 상실·driver 오류와 입력 거부 | worker가 고정 heartbeat 오류로 종료, 모델 로딩 없음. driver 원문 미노출 |
| provider unknown과 heartbeat 오류 동시 발생 | worker는 stale 실행 오류를 우선해도 원장 unknown은 유지. 같은 작업의 usage 상태를 분류 helper에 공급하면 usage_pending, retryable=null 유지 |

정상 사례의 두 번째 run_once는 합성 큐가 작업 없음을 반환하는 경우다. 이 결과를 PostgreSQL의
멱등 claim·재시작·동시 worker 보장으로 확대하지 않는다. heartbeat 테스트에서도 합성 queue가
입력 허용 상태를 함께 거부하도록 구성했다. 실제 A의 원장/heartbeat callback 연결 증거가 아니다.

## 검증 결과

Windows / Python 3.12.14:

- 변경 전 병합 main 기준: **530 passed, 13 subtests passed**.
- 보완·새 조합 회귀 후: **543 passed, 13 subtests passed**.
- adapter 80개와 새 worker 조합 11개: **91개**. 원래 adapter 78개에 결함 재현/정상 대조 2개를 추가했다.
- Ruff·공백·상대 링크·변경 범위 검사 통과.

```powershell
python -X utf8 -m pytest tests/unit/test_processing_outcomes.py tests/unit/test_processing_embedder.py tests/unit/test_processing_worker_boundaries.py tests/unit/test_remote_processing_stages.py tests/unit/test_remote_processing_worker.py tests/unit/test_pdf_extractor.py tests/unit/test_pdf_extractor_errors.py tests/unit/test_chunker.py tests/unit/translation tests/unit/test_gated_responses.py tests/unit/test_remote_input_dispatch.py tests/unit/test_execution_budget.py -q --basetemp=runtime/pytest-517-heartbeat-final
ruff check src/solo_leveling/infrastructure/embeddings/processing_embedder.py tests/unit/test_processing_embedder.py tests/unit/test_processing_worker_boundaries.py
git diff --check
```

## 남은 A·C 연결과 한계

- A의 큐·DB·원장·게시·배포 코드와 C의 MCP/카드는 수정하지 않았다.
- 실제 원장의 같은 owner/job/attempt usage 상태, 동의·lease·삭제·취소·heartbeat를 올바른
  callback에 연결해야 한다. 일반 입력 허용 조회만으로 usage 정산 완료를 추정하면 안 된다.
- heartbeat 오류가 provider unknown 예외를 가리는 경우에도 실제 원장을 조회해 비용 보류를
  보존해야 한다. 이는 취소/실행 세대 상태를 usage_pending으로 덮어쓰라는 뜻이 아니다.
- actual loader·고정 artifact·tokenizer/pooling·버전·CPU/FP32 호환과 모델 수명·RSS·시간은 미검증이다.
  모델의 실제 로딩/추론·다운로드·export나 외부 API는 실행하지 않았다.
- 실제 PostgreSQL/R2·동시 worker·프로세스 재시작·late usage·원자 게시·기존 context/근거 보존과
  C 사용자 안내의 staging 종단 검증은 여전히 남는다. 원본 DB/PDF/벡터를 읽거나 바꾸지 않았다.

#517은 열어두며, 이 결과를 실제 원격 worker의 완성·게시 성공·배포 승인으로 표현하지 않는다.
