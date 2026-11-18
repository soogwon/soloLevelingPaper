# 원격 KG 읽기 연결 검증 (#589)

## 1차 계약

- product reads 활성화 시 `get_learning_path`, `get_prerequisites`를 등록한다.
- 인증 papers:read·소유자·기존 고정 실행 context가 필요하다. 학습 레코드를 만들지 않는다.
- context의 publication → 원본 job authorization → 동일 소유자·논문·판본의 verified upload intent SHA-256을 사용한다. 모델의 제목·paper key·로컬 파일은 신뢰하지 않는다.
- 패키지의 검수 시드와 기존 domain 계산·view를 사용하며 시작 시 한 번 로드한다. 개인 결과 공용 캐시는 없다.
- 초기 목적은 understand 고정이다. known_concepts 생략은 빈 목록이며 50개·각 200자, 정규화한 중복은 거부한다. 조회로 학습 완료를 저장하지 않는다.
- 검수 해시 일치 ready, 미일치 no_graph. 모든 정상 응답에 graph_revision_id를 포함한다. 없는 context/개념 NOT_FOUND, 잘못된 입력 INVALID_ARGUMENT, 시드·저장소 오류 KNOWLEDGE_GRAPH_UNAVAILABLE.
- 원격 arXiv 동일 논문 식별은 아직 검증·저장되지 않아 related_version을 추측하지 않는다. 이 계약과 목적/known 영속 개인화는 후속 범위다.

## 자동 검증

- 원격 인증·PostgreSQL·KG/웹 회귀 64개 통과.
- 실제 합성 worker 결과 게시 → 검증 업로드 해시 조회 → ready 경로 → 타 소유자 거부 추가 1개 통과.
- 해시가 없는 게시/미검수 PDF는 빈 경로, 같은 시드의 로컬 계산과 경로·skipped 일치, 선수 조회, 입력 상한·정규화 중복·시드 실패를 검증한다.
- 실제 외부 AI·PDF 전송·staging 설정 변경은 하지 않았다.

## staging 적용 전 승인 필요

기존 MCP 역할에 아래 **SELECT만** 추가해야 한다. 현재 PR에서는 SQL 실행·배포를 하지 않는다.

- server_processing_job_authorizations
- server_remote_upload_intents

기존 publication/context 권한은 재사용한다. 새 조회에는 업로드 파일명·저장 경로·동의 정보가 반환되지 않으며 해시는 시드 매칭 내부에서만 쓴다. 적용 실패 시 새 SELECT만 회수하고 배포 실패 시 이전 웹으로 복원한다. worker와 예산·동의는 변경하지 않는다.

실제 Claude 도구 목록·검수/미검수 PDF·두 계정 격리 검증은 아직 미완료다. 검수 PDF의 기존 게시 여부를 확인하고 실제 업로드가 필요하면 별도 승인한다. #589 자동 종료 문구를 사용하지 않는다.
