# 읽기 후 질문과 전후 비교 — C 연결 검증

## 기준과 의존성

- #53의 A/B 합의: 현재 저장 답변 비교, 고정 ID의 3개 묶음과 1대다 관계, 자동 이해도·해결 판정 제외.
- PR #157의 `2f571f7774b3c2f2adea6dced48a9f77a0b6e34f`를 기반으로 연결했다.
- 작업 브랜치: `feat/issue-53-reading-comparison`. PR #157 병합 및 B 인계를 확인했고, 현재 기준은 main `d94b8c2`다. 분기 시점과 병합 후 소스 트리는 동일하며 작업 변경을 보존해 fast-forward했다.
- A/B의 도메인·저장·서비스 파일은 이번 C 변경에서 수정하지 않는다.
- B가 공유한 동시 삭제 시 미작성으로 보일 수 있는 저장소 문제는 #161에서 별도로 추적한다.

## 호출과 화면

`get_reading_questions(context_id, phase="after")`는 읽기 후 4개 질문을 반환한다. `save_reading_answer`에도 `phase="after"`를 명시한다. 두 도구 모두 phase를 생략하면 기존처럼 before다. after 질문은 review 단계에 속한다.

`get_reading_comparison(context_id)`는 `ReflectionService.get_comparison`을 한 번 호출한다. 전후 서비스를 따로 조회해 합치지 않는다. 결과에는 context_id, groups, before/after 세션과 집계를 포함한다.

- hypothesis_vs_paper: before.hypothesis → after.claim, after.evidence
- background_vs_limitation: before.background → after.limitation
- expectation_vs_application: before.expectation → after.application

읽기 질문 화면은 입력 phase에 맞춰 조회·저장한다. 비교 화면은 읽기 전후 문구, 작성 상태, 마지막 수정 시각과 양쪽 집계를 표시한다. 저장 행이 없는 미작성과 빈 초안을 구분한다. 빈 초안도 작성 수에는 포함한다. 완료한 limitation의 의문이 해결됐다고 판정하지 않는다.

읽기 전 답변도 나중에 수정할 수 있으므로 비교는 현재 저장본임을 표시한다. 비교 화면에는 저장 기능이 없다. 오류·다른 context·불일치 응답은 표시하지 않고, 취소·context 변경 후 늦은 재조회 응답도 무시한다. 사용자 답변은 HTML이 아닌 텍스트로 표시한다.

## 검증 결과 — 2026-11-03 macOS

- Python 3.12.14: 아래 서버·MCP·실제 stdio 테스트 373개 통과.
- Node 화면 동작 테스트: 아래 15개 통과.
- 잠금 파일의 의존성으로 두 화면 번들 재생성 성공. 새 비교 화면은 읽기 질문 화면과 동일한 잠금 버전을 사용한다.
- wheel 빌드 성공. wheel의 두 HTML과 비교 화면 라이선스가 소스 배포 자산과 바이트 단위로 일치함을 확인했다.
- 기존 Python 환경에 setuptools가 없어 비격리 빌드는 실패했다. 임시 격리 빌드 환경에서는 성공했다.
- 새 wheel[local]을 별도 가상환경에 설치하고 저장소 밖에서 실행하는 통합 테스트 1개 통과(6.42초). 실제 stdio 서버의 도구 목록, 읽기 전후 화면 리소스·라이선스, 빈 DB 응답과 안전한 오류를 확인했다. 시스템 패키지 공유 없이 설치했고 소스 PYTHONPATH는 사용하지 않았다.
- Claude Desktop 4.4212.0에서 합성 DB와 설치본을 연결해 after 질문 조회 → 답변 저장 → 비교 조회를 실제 실행했다. SQLite 재조회로 저장 내용과 집계도 확인했다.
- Claude 채팅에는 도구 응답과 표가 표시됐지만 MCP 입력·비교 카드는 표시되지 않았다. 개발자 모드에서도 로컬 카드 미리보기 진입점을 찾지 못해 카드 버튼 클릭 검증은 미완료다. 이 관찰만으로 서버 구현 오류나 호스트 미지원으로 단정하지 않는다.
- 임시 커넥터 설정은 원본 바이트로 복원했고 개발자 모드를 비활성화한 뒤 Claude를 재시작했다. 실제 사용자 논문은 사용하지 않았다.
- Windows/Linux의 새 C 연결과 실제 카드 상호작용 검증이 남아 있어 #53은 종료하지 않는다.

```sh
python -m pytest tests/unit/test_reflection.py tests/unit/test_reflection_service.py tests/unit/mcp tests/integration/test_mcp_stdio.py -q
node --test ui/reading-before/reading.test.mjs ui/reading-comparison/comparison.test.mjs
SOLO_INSTALLED_PYTHON=/tmp/solo53-installed/bin/python python -m pytest tests/integration/test_installed_distribution.py -q
```

각 UI 폴더에서 `npm ci` 후 `npm run build`로 배포 자산을 생성한다. 테스트는 실제 SQLite 저장·수정·재조회, context 격리, 1대다 연결, 빈 초안, 단계 필터, 기존 before 호환, 안전한 입력 오류·종료 거부, 화면 저장 실패 및 조회 지연을 다룬다.
