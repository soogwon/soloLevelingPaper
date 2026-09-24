작성·갱신 일시: 2026-09-24 11:40:02 (KST, UTC+09:00)

# 개발 안내

## 등록·번역·색인 연결

등록 흐름은 PDF 페이지 추출 → 페이지 내부 청킹 → 원문 저장 → B 번역 서비스 호출
→ 번역 결과 저장 → 성공한 번역문 임베딩 → Chroma 읽기 검증 → SQLite ready 게시 순서다.

- `register_and_ingest`에는 키워드 인자 `translation_service`, `translation_settings`가 필수다.
- 실제 번역 API 어댑터는 아직 없다. 테스트는 fake 제공자를 명시적으로 주입한다.
- 원문을 한국어 번역으로 복사하거나 원문 임베딩으로 자동 fallback하지 않는다.
- 등록된 논문은 당시 번역·색인을 계속 사용한다. 모델·프롬프트 변경은 새 등록부터 적용한다.
- 같은 `paper_id`와 파일 해시의 게시 완료 등록은 기존 job·리비전·색인 ID를 반환한다.
- 같은 파일도 `paper_id`를 생략하면 새 논문으로 등록된다. 전역 해시 중복 제거는 아직 없다.
- 미게시 실패 등록은 저장된 원문·성공 번역을 재사용한다. 미번역 청크가 있으면
  최초 번역 설정으로만 재시도하고, 동일 번역 리비전의 실패 항목만 채운다.
- 번역은 전체 배치가 끝난 후 저장한다. 번역 도중 종료되면 해당 배치의 미저장 결과는 재사용할 수 없다.
- 일부 번역 실패 시 성공 청크만 색인하고 실패 청크 ID·코드를 limitations에 기록한다.
- 번역 전체 실패 또는 빈 추출 결과는 failed다. 색인 오류 시 저장된 번역은 유지하고 벡터 정리를 시도한다.
- 게시 완료된 부분 번역의 누락 구간 재시도·수동 재번역은 후속 기능이다.
- 같은 논문·파일 해시의 버전 조회·생성과 작업 확보를 하나의 트랜잭션으로 처리한다.
  완료됐으면 기존 결과를, 대기·진행 중이면 기존 job_id와 상태를 반환하며 중복 실행하지 않는다.
  실패·중단된 작업은 이력을 보존하고 새 작업으로 재시도한다.
- `get_ingestion_status`로 작업 상태를 조회하며 ready이면 result에 완료 결과를 포함한다.
  등록·조회 응답의 result_available로 결과 유무를 구분한다. 상태는 조회 시점 기준이며,
  진행 화면 갱신을 위한 C의 API·MCP 노출과 화면 연결은 아직 없다.
- 프로세스 재시작 시
  C의 시작 코드에서 `mark_interrupted_jobs_on_startup`을 호출해야 중단 작업을 재시도할 수 있다.

## 저장 계약

`init_db`는 기존 테이블을 보존하며 번역 메타데이터 컬럼과 신규 테이블을 추가한다.
기존 translation_revisions 행의 새 컬럼은 NULL로 남기며 설정을 추측해서 채우지 않는다.
같은 paper_id·file_hash의 중복 버전을 막는 고유 인덱스도 생성한다.
기존 중복 데이터가 있으면 초기화가 실패하며 자동 삭제·병합하지 않는다. 적용 전에 중복 여부를 확인해야 한다.

| 테이블 | 역할 |
|---|---|
| translation_revisions | 최초 번역의 제공자·모델·프롬프트 버전·언어 |
| translation_results | 청크별 성공/실패 코드. 성공 번역문은 chunks.text에 저장 |
| embedding_sets | 번역 리비전과 임베딩 모델·차원 연결 |
| search_indexes | 벡터 검증 후 게시된 색인과 버전·리비전·job 연결 |
| learning_contexts | 게시 색인에 고정한 학습 목적·known_concepts |
| evidences | context별 청크 및 당시 원문·번역 인용문 |

주요 repository API:

- `prepare_ingestion`: 버전과 작업을 원자적으로 확보하거나 기존 완료·진행 중 작업을 재사용.
  등록 진입점에서 사용하며, 기존 `claim_ingestion_job`은 버전이 이미 있는 내부 호출용으로 유지한다.
- `save_translation_batch`: 전체 배치를 트랜잭션으로 저장. 같은 내용은 재저장 가능.
  성공 번역 덮어쓰기·새 리비전으로 자동 교체·게시된 번역 변경은 거부한다.
- `get_translated_chunks`: 지정한 파싱·번역 리비전의 성공 청크 조회.
- `get_translation_metadata`, `get_translation_failures`: 설정·실패 구간 조회.
- `get_search_index`: 게시 완료된 색인 조회. 선택적으로 정확한 리비전 요구.
- `publish_search_index`: 벡터 검증 이후 호출하는 내부 함수.
  embedding set·색인 참조 저장과 job ready 전환을 한 SQLite 트랜잭션으로 처리.
- `create_learning_context`, `get_learning_context`: context 영속 저장·조회.
  기존 LearningContext에 선택적 embedding_set_id를 추가했으며 저장 시에는 필수다.
- `save_evidences`, `get_evidences`: context 범위·실제 인용문을 검사하고 근거 저장·조회.
  조회는 요청 순서를 보존하며 하나라도 없거나 context 밖이면 전체 요청을 거부한다.

context 생성 시 논문→파싱→번역→게시 색인의 관계를 확인한다.
context를 바꾸는 API는 없으며, 같은 context ID로 새 설정을 덮어쓰지 않는다.
현재는 답변 본문·claim 이력 테이블이 없고 context와 근거만 보존한다.

Chroma는 `chunks-{embedding_set_id}` 컬렉션에 저장한다. 논문별 모델 차원 변경이
다른 등록의 색인과 충돌하지 않도록 분리하며, query_similar는 이 이름을 기본으로 사용한다.
SQLite의 get_search_index로 고정 리비전의 embedding_set_id·모델·차원을 얻어 검색해야 한다.
현재 B의 실제 검색 서비스와 MCP 도구는 아직 연결되지 않았다.

## 설치·테스트

Python 3.11 이상을 사용한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests -q
```

dev 의존성은 pytest·pdfplumber·Chroma다. 테스트는 실제 SQLite·합성 PDF·임시 Chroma를
사용하고, 번역과 임베딩 계산만 테스트 대역으로 교체한다. 모델 다운로드나 유료 API 호출은 없다.

실제 모델 임베딩·MCP 실행 의존성은 `pip install -e ".[local,dev]"`로 설치한다.
실제 임베딩 모델은 첫 사용에 다운로드가 필요할 수 있다. 번역 제공자는 호출자가 주입해야 한다.

## 서비스 연결 전 확인 사항

아래 항목은 현재 구현의 제약과 후속 연결 과제이며, 해결 완료된 기능이 아니다.

### 1. 중복 요청에서 동일한 논문 ID 유지

중복 방지는 같은 `paper_id`와 파일 해시를 기준으로 동작한다. `paper_id`를 생략하거나
요청마다 다른 ID를 전달하면 같은 PDF도 별도 논문으로 등록된다.
화면·API·MCP 연결 시 같은 등록 시도의 중복 클릭과 재요청에는 동일한 `paper_id`를
사용하도록 호출 흐름을 정해야 한다. 파일 내용만으로 모든 논문의 중복을 제거하는 기능은 없다.

### 2. 부분 번역 완료 안내

일부 청크만 번역에 성공해도 성공한 청크를 색인하고 `ready`로 처리한다.
따라서 `ready`는 모든 청크의 번역 성공을 뜻하지 않는다. 호출부는 `limitations`의
실패 정보를 확인하고 사용자에게 일부 내용이 검색 대상에서 빠졌음을 안내해야 한다.
게시 완료 후 같은 논문을 다시 등록하면 기존 결과를 반환하므로 누락 번역을 채우지 않는다.
게시된 색인의 누락 번역 재시도는 별도 후속 기능이다.

### 3. 번역 중단 시 재호출 비용

현재 번역 결과는 전체 배치가 끝난 뒤 DB에 저장한다. 배치 도중 프로세스가 종료되면
이미 번역 호출에 성공했더라도 저장되지 않은 결과는 재시도에서 다시 번역한다.
DB에 저장된 성공 번역을 재사용하는 동작과 구분해야 한다.
실제 유료 번역 API를 연결하기 전에 작은 배치 단위 저장과 재시도 비용 제한을 검토한다.

### 4. 재시작 시 중단 작업 정리

강제 종료되면 작업이 DB에 `processing`으로 남을 수 있다. 이를 정리하지 않으면
이후 중복 요청도 계속 기존 작업이 진행 중이라는 응답을 받는다.
C의 시작 코드에서 `mark_interrupted_jobs_on_startup()`을 호출하는 연결을 확인해야 한다.
단, 같은 DB를 사용하는 다른 작업 프로세스가 실제로 처리 중일 때 일괄 중단 처리하면 안 된다.
호출은 기존 처리 프로세스가 종료되었음을 확인한 시작 시점으로 제한하고,
여러 프로세스가 동작하는 구조라면 작업 소유권·생존 여부를 확인하는 별도 정책이 필요하다.

우선 확인할 사항은 동일한 논문 ID 유지, 부분 번역 안내, 재시작 처리 연결이다.
번역 중간 저장은 실제 번역 API 연결 전에 검토한다.

## 알려진 제약

- 임베딩 모델 ID·리비전 확정과 실제 번역 품질 평가는 별도 작업이다.
- 인쇄 페이지 번호 자동 인식은 없으며 printed_page_label은 보통 NULL이다.
- PDF의 runtime/pdfs 복사, import 루트 검사, URL 등록, MCP 연결은 아직 없다.
- 기존 원문 기반 임시 색인은 search_indexes에 자동 등록하지 않는다.
  명시적인 등록 재시도로 새 번역 색인을 준비해야 한다.
- 실패한 벡터 정리까지 실패하면 index_cleanup_failed를 남긴다.
  프로세스 강제 종료로 남은 미게시 벡터의 전역 정리는 후속 작업이다.
- 외부 모델 비용 제한·재시도 정책·동시 색인 전체 제한은 호출 계층에서 추가해야 한다.

---

# 개발 안내

- 문서 최초 커밋 일시: 2026-09-15 23:25:29 (KST, UTC+09:00)
- 최초 커밋: `e0dc109f97836adafee8b35ed082e7f67c143af5`
- 작성자: `cholholim`
- 아래 내용은 이전의 내용을 보존한 것으로 최신의 내용은 위쪽의 문서 내용을 참조

## 현재 구현 범위 (A: PDF·데이터)

이 커밋은 21번 문서(3인 개발 분담)의 **A 담당 1주차 산출물** — "PDF→원문·페이지·벡터 저장"을 구현한다.

**구현 완료**:
- `infrastructure/parsing/pdf_extractor.py` — pdfplumber로 페이지 보존 추출
- `infrastructure/parsing/chunker.py` — 페이지 경계를 넘지 않는 청킹
- `infrastructure/database/` — SQLite 스키마·리포지토리 (papers, paper_versions, parse_revisions, chunks, embedding_sets, processing_jobs)
- `infrastructure/embeddings/embedder.py` — sentence-transformers 래퍼 (모델 ID는 잠정값, 팀 합의 필요)
- `infrastructure/storage/vector_store.py` — ChromaDB 저장·검색
- `workers/ingestion.py` — 등록→파싱→(임시 번역 통과)→임베딩→저장 오케스트레이션

**아직 없음**:
- 실제 번역 파이프라인 (B 담당) — 현재는 원문을 그대로 임베딩하는 임시 통과 리비전으로 대체
- MCP 도구 인터페이스 (C 담당, `interfaces/mcp/`)
- URL 등록, import 루트 경로 보안 검증 (C 담당 영역)
- `translation_revisions` 테이블에 실제 레코드 저장 (현재 `workers/ingestion.py`는 임베딩 세트 ID 생성에만 임시 리비전을 쓰고 테이블에는 저장하지 않음 — B 파이프라인 연동 시 정리 필요)

## 설치

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

## 테스트

```bash
pytest tests/ -v
```

단위 테스트(`tests/unit/`)는 네트워크 없이 동작한다. 통합 테스트(`tests/integration/`)는
실제 환경에서는 sentence-transformers 모델을 huggingface.co에서 받아야 하므로 최초 실행 시
인터넷 연결이 필요하다(개발 샌드박스에서는 mock으로 대체해 검증했다).

## 알려진 제약 (팀 공유용)

- 임베딩 모델 ID(`paraphrase-multilingual-MiniLM-L12-v2`)는 잠정값 — 22번 문서에서 "정확한 모델 ID·리비전"이 미결정 사항으로 명시되어 있어 팀 합의 필요
- `printed_page_label`(인쇄된 페이지 번호) 자동 인식 로직은 미구현 — 현재 항상 `None` 반환 (22번 문서: "자동 인식 방식은 미정")
- PDF를 `runtime/pdfs`로 실제 복사하는 로직 미구현 — 현재 `stored_path`는 원본 경로를 그대로 저장 (C 담당 영역과 합의 후 추가)
- 번역 단계는 임시 통과(passthrough)로 구현되어 있어, B의 실제 번역 파이프라인이 준비되면 `workers/ingestion.py`의 해당 부분을 교체해야 함
