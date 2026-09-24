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
- 일부 번역 실패 시 성공 청크만 색인하고 실패 청크 ID·코드를 limitations에 기록한다.
- 번역 전체 실패 또는 빈 추출 결과는 failed다. 색인 오류 시 저장된 번역은 유지하고 벡터 정리를 시도한다.
- 게시 완료된 부분 번역의 누락 구간 재시도·수동 재번역은 후속 기능이다.
- 같은 버전의 동시 등록은 REQUEST_IN_PROGRESS로 거부한다. 프로세스 재시작 시
  C의 시작 코드에서 `mark_interrupted_jobs_on_startup`을 호출해야 중단 작업을 재시도할 수 있다.

## 저장 계약

`init_db`는 기존 테이블을 보존하며 번역 메타데이터 컬럼과 신규 테이블을 추가한다.
기존 translation_revisions 행의 새 컬럼은 NULL로 남기며 설정을 추측해서 채우지 않는다.

| 테이블 | 역할 |
|---|---|
| translation_revisions | 최초 번역의 제공자·모델·프롬프트 버전·언어 |
| translation_results | 청크별 성공/실패 코드. 성공 번역문은 chunks.text에 저장 |
| embedding_sets | 번역 리비전과 임베딩 모델·차원 연결 |
| search_indexes | 벡터 검증 후 게시된 색인과 버전·리비전·job 연결 |
| learning_contexts | 게시 색인에 고정한 학습 목적·known_concepts |
| evidences | context별 청크 및 당시 원문·번역 인용문 |

주요 repository API:

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

## 알려진 제약

- 임베딩 모델 ID·리비전 확정과 실제 번역 품질 평가는 별도 작업이다.
- 인쇄 페이지 번호 자동 인식은 없으며 printed_page_label은 보통 NULL이다.
- PDF의 runtime/pdfs 복사, import 루트 검사, URL 등록, MCP 연결은 아직 없다.
- 기존 원문 기반 임시 색인은 search_indexes에 자동 등록하지 않는다.
  명시적인 등록 재시도로 새 번역 색인을 준비해야 한다.
- 실패한 벡터 정리까지 실패하면 index_cleanup_failed를 남긴다.
  프로세스 강제 종료로 남은 미게시 벡터의 전역 정리는 후속 작업이다.
- 외부 모델 비용 제한·재시도 정책·동시 색인 전체 제한은 호출 계층에서 추가해야 한다.
