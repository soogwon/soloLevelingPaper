# 복구 작업의 원격 목록과 단건 조회

#594와 #595의 조회 연결입니다. 웹 논문 상세의 처리 작업 목록과 Claude의 기존 목록·단건 도구가 같은 상태를 반환합니다. 웹 세션의 단건 경로는 `GET /v1/processing-jobs/{job_id}`입니다.

복구 작업은 기존 UUID 커서에 포함되며 후보 게시 때 생성된 내부 embed 행과 중복 표시되지 않습니다. 응답은 게시 대기, 후보 게시, 새 학습 맥락 선택을 구분합니다. 원장에 없는 revision·attempt·갱신 시각은 null입니다. `web_path`는 해당 작업의 기존 검토 화면과 동일 요청 키를 열고, 취소나 색인 선택은 그 화면에서 수행합니다.

배포 시 복구 웹 설정과 원격 제품 조회가 활성화된 환경에서만 `REMOTE_REPROCESSING_JOB_READS_ENABLED=true`를 설정합니다. 먼저 기존 원격 제품 조회 역할의 다음 열 권한과 실행 계획을 읽기 전용으로 확인합니다.

- `server_reprocessing_reviews`의 `owner_user_id, job_id, context_id, operation, state, created_at, publication_id, request_key, selected_context_id`에 열 단위 SELECT
- 기존 `server_execution_contexts`, `server_papers`, `server_processing_publications` 조회 권한 유지
- 복구 원장의 원문·번역 결과, 동의, 예산 및 쓰기 권한은 조회 연결에 필요하지 않습니다.

플래그의 기본값은 비활성입니다. 조회에 문제가 생기면 해당 플래그를 끄고 기존 처리 작업 조회 경로로 복구합니다. 실제 staging 적용 및 Desktop 확인은 후속 검증입니다.
