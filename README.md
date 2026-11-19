# Solo Leveling Paper

> MCP와 Knowledge Graph를 이용하는 개인화 AI 논문 학습 도구

Solo Leveling Paper는 논문 원문, 검수된 개념 관계, 근거가 연결된 학습 기록을 Claude에서 활용하고 웹 아카이브에서 다시 확인할 수 있게 하는 프로젝트입니다. 현재는 **원격 MCP(스테이징)**와 **Claude Desktop 로컬 MCP**를 함께 운영합니다.

> [!WARNING]
> 아직 개발·검증 중인 프로젝트입니다. 스테이징 데이터와 기능은 변경될 수 있으며 프로덕션 SLA를 제공하지 않습니다. 비공개 논문을 등록하기 전에는 화면에 표시되는 저장·외부 전송 범위를 확인하세요.

## 주요 기능

- PDF 등록, 파싱, 번역 및 의미 검색
- 논문 근거를 포함한 질문·답변
- 검수된 Knowledge Graph 기반 선수 개념과 학습 경로
- 메모, 하이라이트, 요약 및 읽기 상태 저장
- 웹 아카이브를 통한 논문과 학습 기록 조회
- 계정별 데이터 격리와 명시적 저장·AI 전송 동의

기능마다 원격 전환과 검증 단계가 다릅니다. 세부 지원 범위는 [문서 안내](doc/README.md)와 GitHub 이슈를 기준으로 확인합니다.

## 설치 방식 선택

| | 원격 MCP | Claude Desktop 로컬 MCP |
| --- | --- | --- |
| 권장 대상 | 스테이징 사용자, 여러 기기에서 같은 기록을 쓸 사용자 | 로컬 데이터와 모델을 직접 관리할 개발자·테스터 |
| 설치 | Claude에 커넥터 URL 추가 | Python 환경과 로컬 MCP 패키지 설치 |
| PDF 등록 | Solo Leveling 웹에서 업로드 | `DATA_ROOT/imports`에 복사한 뒤 Claude에서 등록 |
| 실행·저장 위치 | Solo Leveling 서버 | 사용자 PC |
| OpenAI API 키 | 사용자 키 불필요 | 외부 번역·생성을 켜면 사용자 키 필요 |
| 현재 상태 | 스테이징·시험 운영 | Windows/Python 3.12 우선 검증 |

- 서버를 통해 간단히 연결하려면 [원격 MCP 연결 가이드](doc/setup/remote-mcp-installation.md)를 따릅니다.
- PC 안에서 실행하려면 [Claude Desktop 로컬 MCP 설치 가이드](doc/setup/desktop-installation.md)를 따릅니다.
- 화면으로 보는 전체 절차는 [웹 설치 가이드](web/landing/mcp.html)에도 정리되어 있습니다.

## 빠른 시작: 원격 MCP

원격 MCP는 Python, 로컬 검색 모델, 개인 OpenAI API 키 없이 사용할 수 있습니다.

1. [스테이징 웹](https://solo-leveling-staging.soogyung.com)에 로그인하거나 계정을 만듭니다.
2. Claude의 **설정 → 커넥터(Connectors) → 사용자 지정 커넥터 추가**를 엽니다.
3. 다음 주소를 등록합니다.

   ```text
   https://solo-leveling-staging.soogyung.com/mcp
   ```

4. **연결**을 누르고 1단계와 같은 Solo Leveling 계정으로 로그인한 뒤 권한을 승인합니다.
5. 웹의 PDF 등록 화면에서 파일 보관과 AI 전송 범위를 확인하고 각 단계에 동의합니다.
6. Claude에서 “내 논문 목록을 보여줘” 또는 “연결 상태를 확인해줘”라고 요청합니다.

채팅에 첨부한 PDF가 원격 MCP로 자동 전달되지는 않습니다. 원격 논문은 웹에서 업로드해야 합니다. 현재 주소는 스테이징 전용이며 기능 플래그나 배포 상태에 따라 일시적으로 사용할 수 없을 수 있습니다.

자세한 계정 일치, 재연결, 처리 상태 확인과 문제 해결은 [원격 MCP 연결 가이드](doc/setup/remote-mcp-installation.md)를 참고하세요.

## 빠른 시작: Claude Desktop 로컬 MCP

### 준비물

- Claude Desktop
- Python 3.12 권장(패키지 메타데이터의 최소 버전은 3.11)
- Git
- 수 GB의 여유 디스크 공간
- 외부 번역·답변 생성을 사용할 경우 OpenAI API 키

### 설치

아래 예시는 Windows PowerShell입니다.

```powershell
$root = Join-Path $env:LOCALAPPDATA 'SoloLevelingPaper'
py -3.12 -m venv "$root\venv"
& "$root\venv\Scripts\python.exe" -m pip install "solo-leveling-paper[local] @ git+https://github.com/soogwon/soloLevelingPaper.git"
```

`$root\settings.env`를 만들고 경로와 정책을 지정합니다. 경로는 환경 변수나 `~` 대신 절대 경로를 사용하세요.

```dotenv
DATA_ROOT=C:/Users/사용자/AppData/Local/SoloLevelingPaper/data
HF_HOME=C:/Users/사용자/AppData/Local/SoloLevelingPaper/models
OPENAI_API_KEY=본인의_API_키
GENERATION_MODEL=gpt-5.4-mini
TRANSLATION_MODEL=gpt-5.4-mini
LOCAL_ONLY=false
ALLOW_EXTERNAL_API=true
```

`ALLOW_EXTERNAL_API=true`이면 등록·질문 과정에서 논문 일부가 외부 AI API로 전송되고 사용자 API 비용이 발생할 수 있습니다. 키가 포함된 설정 파일은 커밋하거나 공유하지 마세요.

설정을 점검하고 검색 모델을 준비한 뒤 Claude Desktop 설정 조각을 출력합니다.

```powershell
& "$root\venv\Scripts\python.exe" -m solo_leveling.interfaces.cli.desktop check --env-file "$root\settings.env"
& "$root\venv\Scripts\python.exe" -m solo_leveling.interfaces.cli.desktop prepare-model --env-file "$root\settings.env"
& "$root\venv\Scripts\python.exe" -m solo_leveling.interfaces.cli.desktop config --env-file "$root\settings.env"
```

마지막 명령이 출력한 `mcpServers` 항목을 `%APPDATA%\Claude\claude_desktop_config.json`에 **기존 설정과 합쳐서** 저장하고 Claude Desktop을 완전히 종료한 뒤 다시 실행합니다. 원격 MCP URL은 이 JSON 파일에 넣지 않습니다.

상세 설치, wheel 배포, 오프라인 모델 준비, 업그레이드와 문제 해결은 [로컬 MCP 설치 가이드](doc/setup/desktop-installation.md)를 참고하세요.

## 동작 구조

```text
원격: Claude ── OAuth / MCP ── Solo Leveling API·Worker ── PostgreSQL·Object Storage
                                      │
                                      └── 웹 아카이브

로컬: Claude Desktop ── stdio MCP ── 사용자 PC의 데이터·모델
                                      └── (선택) 동의한 기록만 서버 동기화
```

원격과 로컬은 같은 목적을 제공하지만 설치, 데이터 위치, 인증과 비용 주체가 다릅니다. [병행 전환 결정](doc/architecture/24-hybrid-server-transition.md)과 [원격 MCP 설계](doc/architecture/25-remote-mcp-transition.md)에 설계 배경이 있습니다.

## 저장소 구조

```text
soloLevelingPaper/
├─ src/                 # Python 도메인·서비스·MCP·HTTP 서버
├─ web/                 # 웹 아카이브와 랜딩·설치 안내
├─ skills/              # Claude 학습 Skill 자원
├─ migrations/          # PostgreSQL 스키마 마이그레이션
├─ tests/               # 단위·통합·계약 테스트
├─ evaluation/          # 검색·생성 품질 평가 자료
├─ deploy/              # 배포 보조 파일
├─ doc/                 # 설계, 설치, 운영 문서
└─ mini-projects/       # 초기 기술 검증 기록
```

## 개발

```powershell
git clone https://github.com/soogwon/soloLevelingPaper.git
cd soloLevelingPaper
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,local,server]"
& .\.venv\Scripts\python.exe -m pytest
```

개발 환경, 테스트 묶음과 PR 규칙은 [개발 안내](doc/setup/development.md)와 [CONTRIBUTING.md](CONTRIBUTING.md)를 따릅니다.

## 보안과 저작권

다음 항목은 저장소에 커밋하지 않습니다.

- API 키, access/refresh token과 `.env` 파일
- 사용자 프로필, 학습 이력 및 개인정보
- 재배포 권한이 없는 논문 원문과 비공개 자료
- 로컬 데이터베이스, Knowledge Graph 저장 파일, embedding cache와 모델 파일
- 민감정보가 포함된 로그

원 논문과 데이터셋의 라이선스 및 이용 조건을 확인하고, 생성 결과에는 가능한 범위에서 출처와 근거를 표시합니다. 보안 문제는 공개 이슈에 비밀값을 붙이지 말고 저장소 관리자에게 비공개로 전달하세요.

## 프로젝트 정보

- 과정: 생성 AI 응용 서비스 개발자 양성 과정(10회차)
- 팀: 3팀 김치GPT
- 개발 기간: 2026-08-01부터 진행 중

현재 구현과 결정 사항은 [문서 안내](doc/README.md), 진행 중 작업은 [GitHub Issues](https://github.com/soogwon/soloLevelingPaper/issues)에서 확인할 수 있습니다.
