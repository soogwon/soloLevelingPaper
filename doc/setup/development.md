# 개발 안내

## 현재 구현 범위 (A: PDF·데이터)

이 커밋은 21번 문서(3인 개발 분담)의 **A 담당 1주차 산출물** — "PDF→원문·페이지·벡터 저장"을 구현한다.

**구현 완료**:
- `infrastructure/parsing/pdf_extractor.py` — pdfplumber로 페이지 보존 추출
- `infrastructure/parsing/chunker.py` — 페이지 경계를 넘지 않는 청킹
- `infrastructure/parsing/url_ingest.py` — URL 등록: 사설망 차단, PDF/HTML 분류, HTML 본문 추출
- `infrastructure/database/` — SQLite 스키마·리포지토리 (papers, paper_versions, parse_revisions, chunks, embedding_sets, processing_jobs)
- `infrastructure/embeddings/embedder.py` — sentence-transformers 래퍼 (모델 ID는 잠정값, 팀 합의 필요)
- `infrastructure/storage/vector_store.py` — ChromaDB 저장·검색
- `workers/ingestion.py` — `register_and_ingest`(PDF), `register_and_ingest_url`(URL, PDF/HTML 자동 분기) 오케스트레이션

**아직 없음**:
- 실제 번역 파이프라인 (B 담당) — 현재는 원문을 그대로 임베딩하는 임시 통과 리비전으로 대체
- MCP 도구 인터페이스 (C 담당, `interfaces/mcp/`)
- **URL 사설망 차단은 최소 방어선만 구현됨** — DNS 리바인딩, 리다이렉트 체인 우회까지 막는 정식 네트워크 정책은 C의 import 루트 보안 로직과 통합 필요
- `translation_revisions` 테이블에 실제 레코드 저장 (현재 `workers/ingestion.py`는 임베딩 세트 ID 생성에만 임시 리비전을 쓰고 테이블에는 저장하지 않음 — B 파이프라인 연동 시 정리 필요)
- URL로 받은 PDF도 `tmp_pdf_dir`(임시 폴더)에만 저장됨 — `runtime/pdfs` 관리 정책 확정 필요 (로컬 PDF와 동일한 미해결 사항)

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
