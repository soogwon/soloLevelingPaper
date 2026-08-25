# Solo Leveling Paper

> MCP·Skill 기반 개인화 AI 논문 학습 어시스턴트

AI/ML 논문의 개념, 방법론, 과제와 논문 간 관계를 Ontology 기반 Knowledge Graph로 구축하고, 사용자가 기존 AI 챗봇 안에서 자신의 수준에 맞게 논문을 학습하도록 지원하는 프로젝트입니다.

## Project Goal

Solo Leveling Paper는 별도의 논문 학습 웹서비스가 아닙니다. 기존 AI 챗봇이 사용자의 수준과 목표를 이해하는 논문 학습 튜터로 동작하도록 지원하는 지식 MCP와 Learning Skill 패키지를 개발합니다.

- AI/ML 논문의 개념·방법론·과제·논문 관계를 Knowledge Graph로 구축
- AI 챗봇이 검증 가능한 지식과 사용자 학습 상태를 조회할 수 있는 MCP 서버 제공
- 선행 개념과 사용자 수준을 반영한 최소 학습 경로 제시
- HTML 교재, 논문 읽기 가이드, 퀴즈와 보충 학습 자료를 생성하는 Skill 구현
- 특정 챗봇의 대화 기록과 지식·사용자 프로필을 분리하여 재사용성과 확장성 확보

## Core Concept

- **MCP**: 검증 가능한 논문 지식, 개념 관계와 사용자 학습 상태를 제공합니다.
- **Learning Skill**: 조회한 지식과 사용자 수준을 바탕으로 개인화된 학습 경험과 산출물을 생성합니다.
- **Ontology / Knowledge Graph**: 단순 인용 수가 아니라 개념의 선행 관계를 표현합니다.
- **Personalization**: 사용자의 현재 수준과 학습 이력을 기준으로 다음 학습 단계를 결정합니다.

## Basic User Scenario

1. 사용자가 기존 AI 챗봇에 MCP와 Skill을 연결합니다.
2. MCP가 사용자 프로필과 대상 논문의 필수 선행 개념을 조회합니다.
3. 사용자에게 필요한 최소 학습 경로를 제시합니다.
4. Learning Skill이 HTML 교재, 논문 읽기 가이드, 퀴즈와 보충 자료를 생성합니다.
5. 학습 결과와 진행 상태를 저장합니다.
6. 다음 접속 시 이전 기록을 조회하여 다음 단계부터 학습을 재개합니다.

## Project Structure

초기 단계에서는 각 팀원이 `mini-projects/{member-name}/`에서 독립적으로 기술을 검증합니다. 검증된 결과는 추후 공통 구조로 통합합니다.

```text
soloLevelingPaper/
├─ README.md
├─ .gitignore
├─ .env.example
├─ CONTRIBUTING.md
└─ mini-projects/
   ├─ member-a/
   ├─ member-b/
   ├─ member-c/
   └─ member-d/
```

각 Mini Project는 필요에 따라 자체 `README.md`, `src/`, `tests/`, `requirements.txt`, `.env.example` 등을 가질 수 있습니다.

## Planned Capabilities (초안)

아직은 초안으로 MVP 범위와 우선순위는 팀 협의 후 확정합니다.

- 사용자 프로필 및 학습 상태 관리
- 논문별 필수 선행 개념 조회
- Ontology 기반 최소 학습 경로 생성
- Knowledge Graph / GraphRAG 기반 지식 탐색
- HTML 교재 및 논문 읽기 가이드 생성
- 개인화 퀴즈와 보충 학습 자료 생성
- 학습 이력 저장 및 이어서 학습 
- 근거와 출처를 포함한 응답

## Project Information

- 프로젝트 기간: 2026-08-01 ~ 2026-11-21
- 과정: 생성 AI 응용 서비스 개발자 양성 과정(10회차)
- 팀: 3팀 김치GPT
- 추진 일정: 추후 확정
- 역할 분담: 추후 확정

## Development Rules

1. 개인 작업은 별도 Branch에서 진행합니다.
2. `main` Branch에는 Pull Request를 통해 병합합니다.
3. 실제 사용자 정보, 학습 이력, 논문 원문과 API Key를 커밋하지 않습니다.
4. 공개와 재배포가 허용된 Sample 데이터만 저장합니다.
5. Mini Project 결과는 검증한 뒤 공통 설계에 맞게 통합합니다.

## Branch Convention

```text
mini/{member}/{topic}
feat/{feature-name}
fix/{bug-name}
docs/{topic}
```

## Security and Copyright

다음 항목은 저장소에 올리지 않습니다.

- API Key와 `.env`
- 사용자 프로필, 학습 이력 및 개인정보
- 재배포 권한이 없는 논문 원문
- 비공개 또는 기관 내부 자료
- 로컬 데이터베이스와 Knowledge Graph 저장 파일
- Embedding Cache와 모델 파일
- 민감정보가 포함된 로그

원 논문과 데이터셋의 라이선스 및 이용 조건을 확인하고, 생성 결과에는 가능한 범위에서 출처와 근거를 명시합니다.

## Status

현재 프로젝트 초기 구성 단계입니다. 기획서의 필수 기능(MVP), 추가 고려 기능, 데이터 수집 계획, 세부 일정과 역할 분담은 팀 협의 후 문서에 반영합니다.

