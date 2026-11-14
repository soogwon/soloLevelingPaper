# 문서 안내

현재 동작과 사용법을 설명하는 문서입니다. 이슈 처리 중 남긴 일회성 검증 기록은 [기록 보관](history/README.md)에 있습니다.

## 설계

- [02. 서비스와 페르소나](architecture/02-product-and-personas.md)
- [Solo Leveling Paper — Git 저장소 구조와 협업 규칙](architecture/04-git-저장소-구조와-협업규칙.md)
- [10. MCP 인터페이스 명세](architecture/10-mcp-contracts.md)
- [21. 팀원 3명 로컬 M1 개발 분담](architecture/21-three-person-development-plan.md)
- [22. M1 로컬 실행 기준과 기술 스택](architecture/22-local-m1-baseline.md)
- [23. Evidence QA 설계](architecture/23-evidence-qa-design.md)
- [24. 로컬 MCP와 중앙 서버 병행 전환 결정](architecture/24-hybrid-server-transition.md)
- [#110 퀴즈 MCP 연결 계약안](architecture/quiz-mcp-contract-proposal.md)
- [전체 메모·하이라이트 저장소 계약 (#206)](annotation-library-contract.md)

## 시작하기

- [개발 안내](setup/development.md)
- [Claude Desktop 로컬 설치·배포 (#58)](setup/desktop-installation.md)
- [로컬 MCP 서버 실행](setup/mcp-server.md)
- [PDF 로컬 실행기](setup/local-paper-runner.md)
- [팀원용 임베딩 모델 준비](setup/embedding-model-preparation.md)
- [로컬 설치의 중앙 서버 연결](setup/local-server-connection.md)

## 서버·배포

- [중앙 HTTP 서버 실행 골격](setup/http-server.md)
- [Render 스테이징 배포](setup/render-staging.md)
- [Auth0 OIDC 설정](setup/auth0.md)
- [외부 웹 로그인과 서버 세션](setup/web-auth-session.md)
- [중앙 서버 인증·소유권 경계](setup/server-auth-boundary.md)
- [서버 PostgreSQL 마이그레이션](setup/server-database-migrations.md)
- [중앙 서버 기기 등록부](setup/server-device-registry.md)

## 서버 동기화

- [서버 동기화·동의·충돌 정책](setup/server-sync-policy.md)
- [서버 동기화 데이터 흐름](setup/server-sync-data-flow.md)
- [서버 논문 메타데이터 등록](setup/server-paper-sync.md)
- [외부 웹 논문 목록 API](setup/server-paper-list.md)
- [외부 웹 논문 상세 API](setup/server-paper-detail.md)
- [서버 학습 기록 동기화 계약](setup/server-learning-record-sync.md)
- [서버 요약 동기화 계약](setup/server-summary-sync.md)

## 논문 처리·검색·답변

- [등록 처리 상태 계약 (#60)](setup/ingestion-outcome-contract.md)
- [OpenAI 번역 연결](setup/openai-translation.md)
- [번역문 판단 기준: translation_results (#30 후속)](setup/translation-source-of-truth.md)
- [부분 번역 실패 보완 계획](setup/translation-failure-recovery-plan.md)
- [#222 실행 서비스와 #223 MCP 연결](setup/translation-recovery-service.md)
- [MCP 임베딩 프로세스 분리](setup/embedding-process.md)
- [회전 문자 제외량 점검 (#26)](setup/rotated-text-metrics.md)
- [근거 부족 시 추가 검색](setup/evidence-search-expansion.md)
- [ask 실행 기록 계약 (저장소 쪽, #119)](setup/ask-execution-contract.md)
- [ask_paper 단계별 진단](setup/ask-paper-diagnostics.md)
- [OpenAI 답변 생성 연결](setup/openai-generation.md)
- [생성 응답 변환과 한국어 평가 자료](setup/generation-evaluation.md)
- [#81 의미 검증 오프라인 평가 기반](setup/semantic-evaluation.md)
- [기존 논문 재파싱과 학습 맥락 전환 (#116)](setup/paper-reparse-contract.md)
- [기존 논문 재파싱 저장 계약 (#116)](setup/reparse-contract.md)
- [B의 명시적 재파싱 실행 서비스 (#116)](setup/reparse-execution.md)
- [#232 PDF 위치 검증과 상세 카드의 이동 범위](setup/pdf-navigation-contract.md)
- [#229 B — 원문 키워드·문장 추출 v1](setup/paper-insight-extraction.md)

## 학습 기능

- [검수 KG 학습 경로](setup/knowledge-graph.md)
- [#61 학습 대시보드 — 1차 범위](setup/learning-dashboard.md)
- [#52 읽기 전 질문 MCP 연결](setup/reading-questions-mcp.md)
- [읽기 후 질문과 전후 비교 — C 연결 검증](setup/reading-comparison.md)
- [퀴즈 시도 목록의 동률 정렬 계약 (#176, #145 후속)](setup/quiz-attempt-order-contract.md)
- [#62 논문 상세 MCP App — 요약·근거 조회 및 기록 작성](setup/paper-detail-app.md)
- [#231 논문 상세의 기록 조회 — B 연결 범위](setup/paper-detail-annotations.md)
- [메모·하이라이트 목록·입력 제한 계약 (저장소 쪽, #56)](setup/annotation-page-contract.md)

## 로그·보안·운영

- [#99 진단 로그 보관 정책과 미리보기](setup/log-retention.md)
- [#99 로그 보관 계약과 검증 계획 — C 협의안](setup/log-retention-contract.md)
- [검증 이력 보존·정리 절차 (#82)](setup/verification-history-retention.md)
- [외부 전송 데이터와 실행 시점 (#64)](setup/external-data-transfer.md)
- [로컬 MCP 보안 위협 모델과 검증 범위 (#64)](setup/security-threat-model.md)
- [URL 응답 수신 크기 제한 (#199)](setup/url-response-size-limits.md)
- [Chroma 지원 버전 정책 (#91)](setup/chroma-support-policy.md)
- [Chroma 종료 결과 계약 (#120, #91 후속)](setup/chroma-close-result-contract.md)
- [B 서비스 연결 안내](setup/b-service-integration.md)
