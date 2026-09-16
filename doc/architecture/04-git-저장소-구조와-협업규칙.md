# Solo Leveling Paper — Git 저장소 구조와 협업 규칙

정리일: 2026-09-07

기존 대화 정리에서 Git·저장소·협업 내용만 분리한 문서다. 구조와 운영 규칙은 제안이며 브랜치 보호·CI·PR 템플릿이 실제 설정되었다는 뜻은 아니다.

## 1. 기본 원칙

- 공통 코드는 저장소 하나에서 관리한다.
- main은 함께 검증한 통합 코드의 기준으로 사용한다.
- 기능별 작은 브랜치를 만들어 짧은 주기로 병합한다.
- 각자 몇 주 동안 개발한 뒤 마지막에 합치지 않는다.
- 폴더는 실제 구현이 필요한 시점에 추가한다.

## 2. 저장소 구조

다음은 확장 가능한 전체 구조 제안이다. 전부 생성된 것은 아니다. 현재 로컬 M1에서 HTTP·웹 화면·원격 인증·Docker 배포는 필수가 아니다.

```text
solo-leveling-paper/
├─ README.md                    # 설치·실행·테스트 안내
├─ .env.example                # 환경변수 예시, 실제 키 제외
├─ .gitignore
├─ pyproject.toml              # Python 패키지·의존성
├─ uv.lock                     # uv 선택·검증 후
├─ Dockerfile                  # 배포 구현 시
├─ compose.yaml                # 필요 시
├─ src/solo_leveling/
│  ├─ interfaces/
│  │  ├─ mcp/
│  │  └─ http/                # 후속 공동 서버용
│  ├─ application/            # 등록·질문 등 실행 흐름
│  ├─ domain/                 # 공통 모델·계약
│  ├─ infrastructure/
│  │  ├─ database/
│  │  ├─ storage/
│  │  ├─ parsing/
│  │  ├─ embeddings/
│  │  ├─ retrieval/
│  │  ├─ generation/
│  │  └─ auth/                # 후속 공동 서버용
│  └─ workers/                # 로컬 M1 내부 작업
├─ web/                        # 필요 시
├─ migrations/                 # DB 변경 이력
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ e2e/
│  └─ fixtures/
├─ evaluation/
├─ docs/
│  ├─ architecture/
│  └─ setup/
└─ .github/
   ├─ workflows/ci.yml
   └─ pull_request_template.md
```

### 실제 생성한 최소 골격

```text
src/solo_leveling/
├─ __init__.py
├─ interfaces/
│  ├─ __init__.py
│  ├─ mcp/__init__.py
│  └─ http/__init__.py
├─ application/__init__.py
├─ domain/__init__.py
├─ infrastructure/__init__.py
└─ workers/__init__.py

pyproject.toml
tests/test_package.py
docs/setup/development.md
```

기존 README·문서·mini-projects는 보존했다. README에 개발 안내 링크를 추가했고 .gitignore에 *.egg-info/ 제외 규칙을 추가했다. 기본 import 테스트 통과는 서비스 구현 완료를 뜻하지 않는다.

## 3. 브랜치 운영

3명·3주 개발에서는 main과 작업 브랜치만 운영하고 별도 develop은 두지 않는 제안이다.

```text
main
├─ codex/pdf-ingestion
├─ codex/evidence-qa
├─ codex/local-mcp-setup
└─ codex/page-citation
```

위 이름은 작업 예시이며 실제 생성한 브랜치 목록이 아니다.

| 구분 | 용도 | 규칙 |
|---|---|---|
| main | 검증한 통합 코드 | 직접 push보다 PR 병합 |
| 작업 브랜치 | 기능·수정 하나 | 가능하면 1~2일 안에 병합 |
| 릴리스 태그 | 시연·배포 기준점 | 예: v0.1.0 |

member-a, member-b 같은 사람별 장기 브랜치는 만들지 않는다. 브랜치는 사람보다 작업 단위로 나눈다.

## 4. PR과 병합 순서

1. 최신 main에서 작업 브랜치를 만든다.
2. 기능 하나와 관련 테스트를 작성한다.
3. PR에 변경 내용·이유·검증 방법·남은 제한을 기록한다.
4. 다른 팀원 한 명이 검토한다.
5. 자동 검사를 통과한 뒤 squash merge한다.
6. 병합된 작업 브랜치를 삭제한다.

PR 하나에 서로 무관한 변경을 섞지 않는다. 검증 완료와 미검증 항목을 구분해서 적는다.

## 5. 공동 검토가 필요한 변경

| 담당 | 주 작업 | 공동 검토 |
|---|---|---|
| A | PDF·DB·저장소·임베딩·작업 | 스키마·청크·근거 형식 |
| B | 검색·생성·검증·가이드 | 질문·답변 형식·모델 호출 계약 |
| C | MCP·로컬 설정·설치·통합 | 도구 계약·파일 접근 범위 |

담당 폴더는 독점 소유가 아니다. domain/, migrations/, 의존성·실행 설정은 다른 팀원에게 영향을 주므로 변경 전에 관련 담당자에게 알린다.

## 6. 자동 검사와 CI

최소 구성 제안:

- 코드 형식·정적 검사
- 단위 테스트
- 중요한 통합 테스트
- 배포 이미지가 생긴 경우 빌드 확인

유료 모델 API 평가는 모든 PR에서 실행하지 않는다. 일반 CI는 테스트 대역을 사용하고 실제 모델 품질 평가는 별도로 수행한다. CI와 브랜치 보호는 실제 적용 완료로 기록하지 않는다.

## 7. 커밋 대상과 제외 대상

| 커밋할 것 | 커밋하지 않을 것 |
|---|---|
| .env.example | .env·API 키·인증 토큰 |
| DB migration | 실제 SQLite DB·덤프 |
| 작은 합성·공유 가능한 테스트 자료 | 사용자 PDF·원문·번역문·벡터 |
| 공개 가능한 평가 문항 | 비공개 논문·민감 평가 결과 |
| 선택한 도구의 의존성 lockfile | 가상환경·모델 캐시 |
| 로그 설정 | 사용자 질문·원문·모델 응답 로그 |

로컬 PDF·DB·벡터·모델·로그는 Git에서 제외되는 runtime/에 둔다. 테스트 자료도 공개·재배포 가능 여부를 확인한다.

API 키를 이미 커밋했다면 .gitignore에 추가하는 것만으로 해결되지 않는다. 키를 폐기·재발급하고 노출 이력을 처리해야 한다.

