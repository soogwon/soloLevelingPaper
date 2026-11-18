# 원격 MCP 로컬 준비 검증 (2026-11-18)

기준 main `df1a326`. 사용자 요청의 1~8번 목록을 기준으로 검증한다. 추가 API 호출,
staging 설정 변경, 실제 PDF 전송, 외부 알림 발송은 하지 않는다. #576은 보류한다.

## 1~6번 검증 결과

| 순서 | 범위 | 이번 증거 | 남은 실제 환경 검증 |
| --- | --- | --- | --- |
| 1 | 일반 질문 상시 worker | schema v2 bootstrap, 제한 DB 역할, 계수·생성 대역, 상시 큐 소비·완료 저장, 새 pool/runner 재시작 후 중복 전송 0 | 상시 프로세스 실제 배포·장시간 운영 |
| 2 | 동시성·실패·철회 | 같은 계수 예약 중복 차단, 정책별 claim, 만료/철회 예산 반환, unknown 예약 유지, 늦은 완료 차단 | 실제 호스트 단절·공급자 장애 |
| 3 | 계정 격리 | 합성 verifier + 실제 ASGI/DB의 owner·scope·조회 차단 | 실제 Auth0 두 계정과 Desktop 교차 접근 |
| 4 | 재게시·삭제 | 과거 저장 답변 보존, tombstone 후 재생/늦은 저장 차단, 삭제 lease 세대 검사 | staging 재처리·삭제 리허설 |
| 5 | PDF 처리·검색 | 합성 parser/번역/임베딩 포트 + 실제 DB의 staging·원자 게시·검색·근거 조회 | 실제 다중 페이지 논문의 품질·메모리·비용 |
| 6 | 운영 제어 | 제어 CAS·상태 조회, 처리 권한·동의·예산 SQL 회귀 | staging 제어 적용·비밀정보 회전·백업 복구 훈련 |

환경: macOS, 별도 로컬 Docker PostgreSQL 18 / pgvector 0.8.7, 루프백 연결,
테스트별 임시 schema 생성·삭제. 기존 staging 자격을 사용하지 않는다.
계수·생성은 HTTP 대역만 사용하며 실제 AI 호출은 0이다. 실제 모델 품질 평가가 아니다.

### 실행 결과

- 최초 1~2번: 132 passed. `test_question_bootstrap`, `test_question_worker_cli`,
  `test_remote_question_queue`, `test_render_worker`, `test_question_token_count_postgres`,
  `test_question_worker_lifecycle_postgres`, `test_question_composition_postgres`.
- 상시/재시작 보강 후: 36 passed. `test_question_token_count_postgres`,
  `test_remote_question_queue_postgres`, `test_question_admission_postgres`,
  `test_question_execution_role_postgres`.
- 3~4번: 125 passed. `test_remote_mcp_auth`, `test_remote_mcp_auth0_verifier_script`,
  `test_remote_mcp_auth0_http`, `test_remote_mcp_product_boundary`,
  `test_remote_product_queries_postgres`, `test_remote_saved_evidence_lifecycle_postgres`,
  `test_processing_publication_postgres`, `test_remote_deletion_leases_postgres`,
  `test_remote_deletion_backend_postgres`.
- 5~6번: 82 passed. `test_processing_verification_postgres`,
  `test_remote_processing_worker_postgres`, `test_processing_staging_postgres`,
  `test_remote_answer_postgres`, `test_remote_sources_postgres`,
  `test_remote_retrieval_postgres`, `test_remote_operation_controls`,
  `test_remote_operation_preview_postgres`, `test_processing_authorization_postgres`,
  `test_remote_policy_postgres`.

묶음 간 중복이 있으므로 독립 테스트 총합으로 합산하지 않는다. 해당 실행은 skip 0이며,
ASGI 묶음에는 anyio BlockingPortal 별칭 사용에 대한 의존성 폐기 예정 경고 1건이 있다.
실행 환경 변수는 `LOCAL_ONLY=true`, `ALLOW_EXTERNAL_API=false`,
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` 및 별도 로컬 `SOLO_TEST_POSTGRES_DSN`이다.

## 판정과 후속

A: DB 조건부 쓰기·계정 격리·정산 검증과 실제 운영 설정 검증을 구분한다.
B: 합성 provider/임베딩 통과를 실제 논문 답변 품질이나 비용 실측으로 간주하지 않는다.
C: 합성 인증·ASGI 통과를 실제 두 계정 Desktop 검증으로 간주하지 않는다.

7번 #502는 원자 생성 저장 계층과 C 내부 인증 복구 adapter가 있으나,
exact/recovery/confirm 서버 포트 및 인증 transport·제품 연결을 추가 검토해야 한다.
8번 종료 판단: #388/#390/#395/#396/#397/#399/#400/#517/#502는 이번 대역 결과만으로
닫지 않는다. 완료한 실측은 기존 티켓 기록을 유지하고 위 미검증 범위를 별도로 남긴다.
#576은 별도 보류이므로 이 문서의 실행 범위에 포함하지 않는다.
