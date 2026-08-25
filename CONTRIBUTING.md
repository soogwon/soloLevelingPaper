# Contributing to Solo Leveling Paper

## 1. 기본 원칙

- `main` Branch에는 직접 Push하지 않습니다.
- 기능 추가와 수정은 별도 Branch에서 진행합니다.
- 완료한 작업은 Pull Request를 통해 병합합니다.
- 사용자 정보, API Key, 재배포 권한이 없는 논문 원문은 커밋하지 않습니다.

## 2. Mini Project 진행 방식

초기 단계에서는 각 팀원이 다음 경로에서 독립적으로 기술을 검증합니다.

```text
mini-projects/{member-name}/
```

Mini Project에는 자체 `README.md`를 두고 해결하려는 문제, 사용 기술, 실행 방법, 검증 결과와 제약을 기록합니다.

```text
mini-projects/{member-name}/
├─ README.md
├─ src/
├─ tests/
├─ data/
│  └─ samples/
├─ requirements.txt
└─ .env.example
```

## 3. Branch Naming

```text
mini/{member}/{topic}
feat/{feature-name}
fix/{bug-name}
docs/{topic}
refactor/{topic}
test/{topic}
```

## 4. Commit Message Convention

```text
type: short description
```

- `feat`: 기능 추가
- `fix`: 오류 수정
- `docs`: 문서 변경
- `test`: 테스트 추가 또는 수정
- `refactor`: 리팩터링
- `chore`: 설정 및 기타 작업

## 5. Pull Request

Pull Request에는 변경 내용과 이유, 실행·테스트 방법, 영향받는 영역, 남은 TODO를 포함합니다. 가능하면 하나의 Pull Request에는 하나의 주요 목적만 포함합니다.

## 6. Data, Security and Copyright

다음 항목은 저장소에 올리지 않습니다.

- `.env`와 API Key
- 사용자 프로필, 학습 이력 및 개인정보
- 재배포 권한이 없는 논문 원문
- 비공개 또는 기관 내부 자료
- Knowledge Graph 및 로컬 데이터베이스 저장 파일
- Embedding Cache와 모델 파일
- 민감정보가 포함된 로그

공개 가능한 Sample 데이터만 `mini-projects/{member}/data/samples/`에 저장합니다.

## 7. Integration

Mini Project를 통합할 때는 실험 코드를 그대로 복사하기보다 검증된 설계와 핵심 로직을 정리하고, 팀이 합의한 공통 인터페이스·코드 스타일·테스트 방식에 맞춥니다.

