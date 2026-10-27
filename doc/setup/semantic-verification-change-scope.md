# #36 변경 범위와 후속 분리

## 이번 정리

- 의미 판정·참조·추출·사용자 범위 검사는 유지한다.
- 기본 로그는 주장 판정·최종 처리·실패 체크·오류 코드를 남긴다.
- 정상 세부 체크는 `--detailed-diagnostics`에서만 남긴다. 이 옵션은 원본 stderr 수집을 허용하지 않는다.
- 중복된 ID 기반 의미 판정 이벤트는 제거한다. 후보 ID 추적은 기존 원본 로그 수집 모드에서만 출력한다.
- 보완 프롬프트와 입력 구성은 `repair_instructions.py`, `repair_request.py`로 분리한다.
- 좁은 사례별 방어는 `semantic_guards.py`로 격리한다. 정규식만으로 정답을 승인하지 않으며 기존 회귀 테스트를 유지한다.

## 리뷰·PR 단위

| 단위 | 포함할 구현 | 공유 지점 |
|---|---|---|
| #36 핵심 | 의미 검증 도메인·서비스·제공자, 원문 구절 ID, 설정·응답 수준, 관련 테스트 | `answer.py`, bootstrap, MCP runtime/server |
| 답변 완전성 | `answer_audit.py`, `semantic_guards.py`, 질문 누락·보완 보존 테스트 | 의미 제공자 응답 계약과 서비스 |
| 후속 보완 생성 | `repair_quotes.py`, `repair_request.py`, `repair_instructions.py`, 보완 선택 parser/schema, 앞뒤 보충 및 테스트 | `answer.py`, `ports.py`, `openai_generator.py`, `response_parser.py` |
| 진단 | 안전 로그 계약·출력·래퍼·Desktop 옵션과 테스트 | 위 서비스의 기록 호출 |

이는 소스 책임 분리이며 아직 별도 Git 브랜치·커밋·PR을 만든 상태는 아니다.
공유 파일 전체를 제외하거나 되돌리면 다른 단위가 깨질 수 있다. PR 분리 시 공유 파일의 코드 구간과
제공자 계약을 함께 이동하고 각 분리 상태에서 테스트해야 한다. 보완 기능을 통째로 끄거나 되돌리지는 않는다.

## 이번 티켓에서 제외할 파일

- `.codex-finalizer/`, `demo_videos/`
- `scripts/build_actual_mcp_mockup_videos.py`, `scripts/build_architecture_diagram.py`
- `scripts/build_feature_mockup_images.py`, `scripts/build_mcp_flow_demos.py`, `scripts/build_user_flow_diagram.py`
- `runtime/diagnostics/`의 실측 스크립트·원문 기반 로그, `runtime/test-*/`의 임시 테스트 자료

무관한 파일은 삭제하지 않고 커밋에 포함하지 않는다. 실측 파일을 제품 패키지에 옮기지 않는다.
재현은 합성 원문을 사용하는 `test_repair_quotes.py`, `test_repair_pair_flow.py`,
`test_semantic_grounding.py`, `test_answer_audit.py` 등으로 유지한다.

## 별도 결정이 필요한 사항

- `[6]`에서 다음 후보가 없는 미완결 주장을 경고와 함께 유지하는 기존 정책은 이번 구조 정리로 바꾸지 않는다.
  조건을 추측한 주장이 남았던 실제 사례는 해결 완료가 아니다.
- 청킹 개선과 여러 페이지 청크의 범위 정책은 A와 협의한다.
- 로그 순환·보관 기간은 별도 운영 정책으로 다룬다. 이 정리는 기존 로그를 자동 삭제하지 않는다.
- 프롬프트 정리 후 실제 모델 출력은 자동 테스트만으로 동등성을 보장할 수 없다. 유료 실측은 별도 승인 범위에서 수행한다.
