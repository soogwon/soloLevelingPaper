# #61 학습 대시보드 — 1차 범위

기존 `context_id`로 저장된 학습 정보를 조회하는 읽기 전용 화면이다.
`start_learning`이 반환한 맥락 ID를 `get_learning_dashboard`에 전달한다.
새로고침도 같은 도구·같은 맥락을 사용하며 자동 폴링은 하지 않는다.

## 표시 내용과 데이터 기준

| 항목 | 기준 |
|---|---|
| 논문 | 맥락에 고정된 version의 제목·파일 표시명·학습 목적 |
| 처리 상태 | 맥락의 색인을 게시한 job. 최신 재처리 작업의 상태와 다를 수 있음 |
| 요약 | 같은 context에 저장된 전체 범위(`paper`) 요약과 근거 ID. 없으면 `null` |
| 학습 진행 | preview / structure / deep_read / review 중 완료한 단계 비율(0~1) |
| 퀴즈 | 같은 context의 단계별 시도 횟수·최근 정답 수/문항 수·최근/최고 점수(0~1) |

진행 행이 없으면 `recorded=false`, 첫 단계, 완료 0개로 반환하고 DB에는 저장하지 않는다.
실제로 저장된 0% 상태와 구분한다. 퀴즈 시도가 없으면 점수는 `null`이며 0점과 다르다.
최근 퀴즈 시도는 기존 서비스와 같이 제출 시각, 같은 시각이면 삽입 순서로 결정한다.
요약 조회는 생성이나 의미 검증을 수행하지 않고 저장된 값을 그대로 표시한다.
요약의 근거는 같은 context의 evidence ID인지 확인한다.

한 번의 읽기 전용 SQLite 트랜잭션으로 화면 데이터를 조회한다. 다른 맥락·최신 색인으로
자동 전환하지 않으며 연결 무결성이 맞지 않으면 안전한 오류를 반환한다.
DB가 없다고 빈 DB를 만들지 않는다. 임베딩·생성·채점·단계 완료·사용자 데이터 변경은 없다.

## 공개 계약

- 도구: `get_learning_dashboard(context_id: string)` — 공백 ID 거부, 미등록 ID는 `NOT_FOUND`.
- 출력: `context_id`, `version_id`, `paper_id`, `title`(nullable), `file_display_name`, `goal`,
  `processing`, `progress`, `summary`(nullable), `quiz_scores`. 중첩 스키마는 도구 목록에서 확인한다.
- 처리 상태는 저장된 job 상태이며 실시간 벡터 무결성 검사나 전체 번역 완료 보증이 아니다.
- 조회 속성: 읽기 전용·멱등, 외부 API 호출 없음.
- UI: `ui://solo-leveling/learning-dashboard.html`, `text/html;profile=mcp-app`.
- UI 미지원 시에도 같은 객체를 structuredContent와 JSON text로 반환한다.
- 화면 오류 시 원본 오류 전문 대신 일반 안내를 사용한다. 텍스트는 HTML로 해석하지 않는다.
- 기존 6개 도구 계약은 유지하고 runtime에서 대시보드를 추가해 총 7개 도구를 공개한다.

## 구현과 개발

- `infrastructure/database/dashboard_queries.py`: 단일 스냅샷 조회. 기존 저장·채점 로직과 분리.
- `interfaces/mcp/dashboard.py`: 도구 스키마·리소스 등록.
- `ui/learning-dashboard/`: 화면 원본과 고정 의존성 잠금 파일.
- `interfaces/mcp/assets/learning-dashboard.html`: 빌드 산출물. 직접 수정하지 않는다.

Node.js와 pnpm을 준비하고 아래 명령으로 테스트·빌드한다. 서버 사용자는 Node.js가 필요 없다.

```bash
cd ui/learning-dashboard
pnpm install --frozen-lockfile --ignore-scripts
pnpm test
pnpm run build
```

저장소 루트의 Python 개발 환경에서:

```bash
python -m pytest tests/unit/test_dashboard_queries.py tests/unit/mcp/test_dashboard.py tests/integration/test_mcp_dashboard.py -q
```

wheel을 깨끗한 소스에서 빌드하고 새 가상환경에 `[local]`로 설치한 뒤,
`SOLO_INSTALLED_PYTHON`을 해당 Python 경로로 지정하여 설치 배포 테스트도 실행한다.
설치 테스트가 건너뛰었다면 실제 설치본 검증으로 기록하지 않는다.

## 실제 호스트 확인과 후속 범위

Claude Desktop에서 기존 학습 맥락으로 `get_learning_dashboard`를 호출한 뒤 화면과
도구 결과의 단계·점수·요약을 비교한다. 새로고침이 같은 context를 조회하는지 확인한다.
새 학습 기록, 일부 완료, 전체 완료, 요약·퀴즈 기록 없음, 오류·취소 상태를 구분한다.
합성 데이터 DOM 테스트·브라우저 미리보기는 실제 호스트 연결 검증을 대신하지 않는다.

1차 범위에는 메모·하이라이트(#56), 논문 상세 화면(#62), 질문·퀴즈 풀이 화면(#63),
단계 변경 도구나 요약·퀴즈 생성 도구가 포함되지 않는다. 내부 서비스로 저장된 데이터는
조회할 수 있으나 아직 MCP로 모든 데이터를 생성할 수 있다는 뜻은 아니다.
미구현 이동 버튼은 제공하지 않으며 해당 영역은 준비 중이라고 표시한다.
메모 요약·상세 화면 연결 등 후속 범위가 남아 있으므로 #61 전체를 종료하지 않는다.

## 1차 자동 검증 기록 — 2026-10-27

- 기준: main `817a4bd`에서 분기한 `feat/issue-61-learning-dashboard`의 로컬 변경.
- 환경: macOS 26.7.1 / Apple Silicon / Python 3.12.14.
- 대시보드 조회·MCP 단위 테스트와 기존 MCP 회귀·stdio 테스트: 209개 통과.
- 대시보드 DOM 테스트: 5개 통과. 안전한 텍스트 표시, 없는 기록/0점 구분, 읽기 전용
  새로고침, 다른 맥락/늦은 응답 거부, 오류·취소·시간 초과를 확인했다.
- wheel 소스/HTML 일치 확인 후 임시 격리 가상환경에 설치. 설치 배포 테스트 및
  실제 stdio의 대시보드 데이터 조회 테스트: 2개 통과, 건너뛰기 없음.
- 전체 테스트: 1433개 통과, 1개 실패, 2개 건너뜀, 하위 테스트 74개 통과.
  전체 실행에서는 wheel 환경변수를 지정하지 않아 설치 배포 검증이 건너뛰어졌으며,
  위 별도 실행에서 실제 설치 검증을 완료했다. 다른 건너뜀은 Windows 파이프 관련 테스트다.
- 실패: `test_answer_expansion.py::test_expands_once_with_same_context_and_scope_and_saves_only_final`.
  상세 `evidence_trace`가 없는 상태에서 로그를 기대한다. 수정 전 main의 깨끗한 소스에서도
  같은 테스트가 실패함을 확인했다. 대시보드 범위에서 기존 진단 정책은 변경하지 않았다.
- 합성 데이터를 넣은 Chromium 미리보기로 960px·390px 화면을 확인했다. 모바일 가로 넘침은 없었다. 실제 SDK-호스트 연결 검증은 아니다.
- Windows·Linux는 이번 변경으로 검증하지 않았다. 실제 Claude Desktop 검증은 아래에 기록한다.

## 실제 Claude Desktop 검증 — 2026-10-27

- 환경: macOS 26.7.1 / Apple Silicon / Claude Desktop 3.6556.2.
- 위 로컬 변경을 포함한 wheel 설치본을 `solo-leveling-dashboard-test` MCP 서버로 연결했다.
  별도 임시 데이터 폴더에 합성 학습 기록을 준비하고 실제 호스트의 MCP App 화면을 확인했다.
- 일부 완료 맥락(`ctx`): 완료 2/4단계·진행률 50%, 저장된 합성 요약과 근거 ID,
  preview 퀴즈 1/1문항·100%가 도구 결과와 동일하게 표시됐다.
- 빈 기록 맥락(`demo-empty`): 진행 기록 없음, 전체 요약 없음, 단계별 퀴즈 기록 없음이
  오류 없이 표시됐다. 기록이 없는 점수는 0점으로 표시하지 않았다.
- 실제 화면의 현황 새로고침 후에도 같은 맥락과 값이 유지됐다.
  새로고침 전후 SQLite 파일의 SHA-256이 같아 저장된 기록이 변경되지 않았음을 확인했다.
- 서버는 로컬 전용·외부 API 차단 설정으로 실행했다. 실제 논문 등록, 임베딩,
  요약 생성 및 채점의 전체 흐름을 검증한 것은 아니다. Claude 호스트 자체의 통신은 별개다.
- 실제 호스트에서 전체 완료·오류·취소 상태와 Windows·Linux 실행은 아직 확인하지 않았다.
  오류·취소·시간 초과는 위 DOM 테스트에서 확인한 범위다.
- 테스트 연결은 임시 경로를 사용하므로 정식 설치 설정으로 간주하지 않는다.

## 실제 논문 검증 — 2026-10-28

- 논문: `1706.03762v7.pdf` (Attention Is All You Need).
- 사용자 앱 데이터 폴더에 별도 설치 환경·데이터·모델 캐시를 준비했다.
  로컬 임베딩 모델의 다운로드와 오프라인 샘플 계산, MCP 7개 도구 연결을 확인했다.
- 최초 등록은 외부 API 차단 설정으로 번역 단계에서 실패했다. 현재 구현에는
  로컬 번역 대체 기능이 없으므로 로컬 전용 설정만으로 번역을 포함한 등록을 완료할 수 없다.
- 사용자 승인 후 외부 API를 허용하고 사용자가 직접 키를 입력했다. 기존 실패 이력과
  논문 버전을 유지한 새 작업으로 재처리하여 109개 문단 번역과 검색 색인 생성을 완료했다.
  최종 상태는 `ready`, 제한 사항은 없었다.
- Claude Desktop 재시작과 서버 재연결을 확인했고, 사용자가 실제 논문의 학습 대시보드가
  정상 표시됨을 확인했다. 키·개인 설정·논문 데이터는 저장소에 포함하지 않는다.
- 실제 학습으로 생성한 요약·진행·퀴즈 기록의 갱신 흐름은 이번 확인에 포함하지 않는다.
  해당 값의 표시 검증은 앞선 합성 데이터 테스트 범위다. 메모 요약과 상세 화면 이동도
  후속 범위이며, 이번 1차 구현만으로 #61 전체를 종료하지 않는다.
