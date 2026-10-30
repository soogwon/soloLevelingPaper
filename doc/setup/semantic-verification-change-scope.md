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

- #97에서는 다음 후보가 없거나 보완을 지원하지 않아도 미완결 원문에 의존하는 주장을 제외한다.
  숫자만 있는 줄의 숫자를 근거로 삼은 주장도 제외한다. 다른 유효한 주장은 `partial`로 유지하고,
  남는 주장이 없으면 `insufficient_evidence`와 `extraction_limited`를 반환한다.
  사용자 범위 안에서 앞뒤 조건 구절을 실제로 연결한 경우와 완결 문장만 인용한 경우는 유지한다.
  페이지 밖 문장 보충 정책(#108)은 별도이며 이번 변경에 포함하지 않는다.
- 청킹 개선과 여러 페이지 청크의 범위 정책은 A와 협의한다.
- 로그 순환·보관 기간은 별도 운영 정책으로 다룬다. 이 정리는 기존 로그를 자동 삭제하지 않는다.
- 프롬프트 정리 후 실제 모델 출력은 자동 테스트만으로 동등성을 보장할 수 없다. 유료 실측은 별도 승인 범위에서 수행한다.

## #97 오프라인 검증

`tests/unit/test_answer_service.py`에서 변수 이름만 제시한 조건, "충분히 작을 때"라는 추측,
페이지 번호를 조건으로 삼은 주장, 잘린 원문만으로 만든 `n < d` 주장을 제외하는지 검증한다.
의미 검증 활성·비활성 모두 검사하며, 고정 응답 생성기와 의미 검증기를 사용하므로 실제 API를 호출하지 않는다.
다른 정상 주장 보존, 전부 제외 시 저장하지 않음, `[6]` 범위 준수, `[6, 7]`의 앞뒤 구절 보완,
본문 없이 제외 사유를 남기는 기본 로그도 검증한다.

```powershell
python -m pytest tests/unit/test_answer_service.py tests/unit/test_extraction_coverage.py tests/unit/test_semantic_verification.py tests/unit/test_answer_audit.py tests/unit/test_repair_pair_flow.py -q
```

이 정책은 인용 구절의 완결성을 검사하며 의미 검증을 대체하지 않는다.
완결된 원문을 인용했어도 주장의 의미가 일치한다는 보장은 없으며 기존 의미 검사를 계속 적용한다.

### Claude Desktop 실측 결과 (2026-10-29)

#97 wheel 재설치 후 기존 context로 계산 복잡도 조건과 경로 길이 영향을 함께 질문했다.
아래는 사용자가 전달한 성공 응답의 관찰 결과이며 원문·개인 식별자·실제 로그는 저장하지 않는다.

| 범위 | 상태 / 검증 수준 / 사유 | 확인한 결과 |
|---|---|---|
| `[6]` | `partial` / `model_checked` / `semantic_limited` | 계산 복잡도 조건 주장은 제외되고 경로 길이 주장 및 6페이지 근거 1개는 유지됨 |
| `[6,7]` | `ok` / `model_checked` / 사유 없음 | `n < d` 조건에 6·7페이지 근거를 함께 연결하고 경로 길이 주장도 유지됨 |

`[6]` 응답에서는 경로 길이에 답했는데도 전체 복합 질문을 미답변 항목으로 표시했다.
질문 항목 분리와 보완 내용 보존 경고의 정확성은 별도 확인 대상으로 남긴다.
`semantic_limited`만으로 추출 단계의 정확한 제외 사유를 단정하지 않으며 세부 원인에는 로그 확인이 필요하다.

각 범위의 앞선 Desktop 호출은 컴퓨터 응답 대기 60초 초과로 결과를 받지 못했다.
이후 사용자 호출의 성공은 시간 초과 원인 해결이나 반복 실행의 안정성을 입증하지 않는다.
이번 실측은 미완결 조건 제외와 정상 조건 연결 보존을 확인한 범위로 제한한다.
