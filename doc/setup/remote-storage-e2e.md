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
