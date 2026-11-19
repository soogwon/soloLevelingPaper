# 저장된 키워드·중요 문장 이관

등록 기기 인증으로 `POST /v1/remote-insights/contexts/{context_id}/imports/preview`를 호출해 고정 판본, 섹션, 이관 항목과 다음 revision을 확인한다. 확인 후 같은 본문에서 `confirmed`를 `true`로 바꿔 `POST /v1/remote-insights/contexts/{context_id}/imports`에 제출한다. 동일 입력의 재요청에는 기존 요청 키를 사용한다. 교체에는 새 키와 현재 revision이 필요하다. 본문은 `remote_insights.py`의 `InsightImport` 계약을 따른다.

기능을 켜려면 migration 0062, `REMOTE_INSIGHT_IMPORTS_ENABLED=true`와 독립 쓰기 역할의 `REMOTE_INSIGHT_IMPORT_DATABASE_URL`이 필요하다. 쓰기 역할에는 DB 연결과 스키마 사용, `server_lock_insight_import` 실행, 처리/번역 청크 SELECT, 이관 두 테이블의 SELECT·INSERT, collection의 revision·source·items 열 UPDATE만 준다. 기존 MCP 역할에는 collection SELECT만 추가한다. 쓰기 역할과 MCP 역할을 서로 공유하지 않는다.

기기 MCP에서 `open_paper_insight_import`에 로컬 및 원격 학습 맥락 ID를 지정한다. 기본 범위는 논문 전체이며 섹션은 로컬 섹션 ID를 지정한다. 대상에는 기존 로컬 버전 ID와 파일 SHA-256에 해당하는 준비 완료 업로드와 고정 게시본이 있어야 한다. 저장 항목을 선택하고 미리보기를 누르면 출처 해시와 페이지로 유일한 원격 청크를 확인한다. 선택 항목의 표시문·인용은 카드에서 확인하고 제출할 때만 원격 서버로 전송한다.

카드는 묶음당 최초 이관을 지원한다. 응답 확인에 실패하면 같은 선택을 같은 요청 키로 다시 보낼 수 있다. 기존 묶음의 다른 선택으로 제출하면 충돌하며 자동 교체하지 않는다. 원격 상세 화면은 해당 학습 맥락에 저장한 데이터만 읽는다.
