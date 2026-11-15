# #397 저장·검색 구간 통합 회귀

`test_remote_retrieval_postgres.py`는 실제 A 저장소·원자 게시·context resolver·pgvector를 같은 임시
schema에서 연결한다. 기존 PostgreSQL fixture를 재사용하고 원본 데이터나 운영 schema는 건드리지 않는다.

- 다른 owner·페이지·섹션의 고득점 후보가 있어도 사전 필터 뒤 허용 근거가 top_k에 남는다.
- 새 publication 게시 뒤 과거 context와 원문·검색 ID가 바뀌지 않는다.
- 다른 owner와 미존재 context는 같은 None이다.
- 확정된 벡터의 직접 SQL 변경이 거부된다.
- 논문을 임시 DB에서 삭제하면 과거 binding으로도 context/검색이 복원되지 않고 관련 행이 제거된다.

CI는 기존 pgvector PostgreSQL 16 서비스를 사용한다. 로컬 DSN이 없으면 5개를 명시적으로 건너뛴다.
실행 예: `python -m pytest tests/integration/test_remote_retrieval_postgres.py -q`.
CI나 실제 임시 DB에서 이 다섯 테스트가 실행/통과했는지 확인해야 저장/검색 구간의 통과를 기록할 수 있다.

이 결과는 #397 전체 종단 검증이 아니다. Auth0·MCP 전송·실제 embedding·동의/원장·답변/근거 저장·
호스트 재시작·tombstone/백업 복원·운영 제어는 포함하지 않는다. 특히 실제 삭제 API나 보존 정책을
결정하지 않았고, 임시 DB의 DELETE를 제품의 사용자 삭제 흐름으로 노출하지 않는다.

#400의 유료 접수/추가 전송 차단·기존 조회 유지, 비밀/로그·배포/롤백은 구현 및 스테이징 선행이 남아 있다.
새 가짜 운영 제어나 임의 한도/삭제 기한을 만들지 않는다. #397·#400은 계속 열어둔다.

## 저장 전 답변 준비와 실제 DB의 조합

`test_remote_answer_postgres.py`의 6개 추가 사례는 실제 context resolver/pgvector 검색을
`RemoteAnswerPreparationService`와 연결한다. 전체 모델 지문을 갖춘 합성 벡터를 사용하며
모델 임베딩·인증 owner 공급·원문 reader·생성·의미 판정은 테스트 대역이다.
source reader는 고정 ID의 저장 원문/번역을 DB에서 읽지만, A의 실제 snapshot/권한/게시 집합
어댑터 구현을 대신하지 않는다. 대역 owner 선택은 실제 OAuth 인증 증거가 아니다.

- 고득점 다른 owner/페이지/섹션의 후보가 있어도 최종 내부 근거 ID는 허용 집합이다.
- 원문·번역 인용문은 저장된 값이며 준비만으로 server_processing_evidences 행이 늘지 않는다.
- 새 publication 후에도 과거 context의 준비 결과 출처·인용문은 유지한다(새 요청의 evidence ID는 새 발급).
- 실제 tombstone 접수 뒤 DB 원문 행이 남아 있어도 준비 요청은 임베딩/source/생성 전에 거부된다.
- 타인/미존재 context는 같은 오류이며, 같은 차원의 다른 runtime 지문도 생성 전에 차단된다.
- 의미 판정 대역이 거부한 주장은 buffer/DB에 남지 않는다. 실제 의미 품질 평가 증거는 아니다.

이 경로는 not_saved 제안값이며 원자 답변/근거 저장·lease 경합·history 저장·예산 원장·provider gate,
Auth0/MCP/호스트·백업 복원은 검증하지 않는다. 제품 삭제의 물리 정리 완료도 주장하지 않는다.
기존 A 삭제 테스트는 그대로 유지하고, 이번에는 삭제 경계를 B 답변 준비까지 연결한 회귀를 추가한다.

실행: `python -m pytest tests/integration/test_remote_answer_postgres.py -q`.
로컬 DSN이 없으면 6개는 명시적으로 건너뛰고 CI의 일회용 PostgreSQL/pgvector에서 확인한다.
이번 PR은 #390 답변 준비 연결 PR 위의 스택이며 그 PR 병합 후 main으로 base를 변경한다.
