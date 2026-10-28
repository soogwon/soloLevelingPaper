# Claude Desktop 로컬 설치·배포 (#58)

## 범위

Windows·Python 3.12를 우선 검증 대상으로 한다. 로컬 단일 사용자 stdio 서버이며,
원격 인증·PDF 업로드·사용자별 접근 격리는 #77에서 별도로 설계한다.
이 서버를 외부에 공개해서는 안 된다. 일반 사용자용 원클릭 설치 프로그램은 아니다.
Python 메타데이터의 최소 버전은 기존 >=3.11을 유지하며 모든 버전·OS의 검증을 뜻하지 않는다.

## 배포 담당자

검증한 커밋에서 wheel을 빌드한다. UI HTML·라이선스는 패키지에 포함되어 있으므로
설치 대상 PC에는 Node나 저장소 소스가 필요 없다.

```powershell
python -m pip wheel . --no-deps --wheel-dir dist
Get-FileHash dist\solo_leveling_paper-0.1.0-py3-none-any.whl -Algorithm SHA256
```

현재 버전은 0.1.0이다. 정식 배포 시 버전·커밋·해시·검증 환경을 함께 기록하고
서로 다른 산출물을 같은 버전으로 배포하지 않는다. PyPI 게시나 GitHub 릴리스 생성은 별도 작업이다.
의존성은 아직 전체 고정되지 않았으므로 wheel만으로 완전한 오프라인·동일 의존성 설치를 보장하지 않는다.

## 신규 설치

Python 3.12와 배포받은 wheel이 필요하다. 아래는 PowerShell 예시다.

```powershell
$installRoot = Join-Path $env:LOCALAPPDATA 'SoloLevelingPaper'
py -3.12 -m venv "$installRoot\venv"
$serverPython = Join-Path $installRoot 'venv\Scripts\python.exe'
& $serverPython -m pip install "C:\Downloads\solo_leveling_paper-0.1.0-py3-none-any.whl[local]"
```

네트워크·디스크 공간이 필요하며 PyTorch 등 모델 의존성의 설치 용량이 클 수 있다.
아래 설정을 `$installRoot\settings.env`에 직접 저장한다. 사용자 이름에 맞게 **절대 경로**를 지정한다.
설정은 평문이므로 현재 사용자만 접근 가능한 폴더에 보관하고 Git·공유 폴더에 올리지 않는다.

```dotenv
DATA_ROOT=C:/Users/사용자/AppData/Local/SoloLevelingPaper/data
HF_HOME=C:/Users/사용자/AppData/Local/SoloLevelingPaper/models
OPENAI_API_KEY=본인의_API_키
GENERATION_MODEL=gpt-5.4-mini
TRANSLATION_MODEL=gpt-5.4-mini
LOCAL_ONLY=false
ALLOW_EXTERNAL_API=false
```

번역·생성에는 외부 API로 논문 일부와 질문·근거가 전송되고 비용이 발생한다.
활성화 전에 [외부 전송 데이터·시점과 설정의 범위](external-data-transfer.md)를 확인한다.
이를 확인한 뒤 실제 사용 시에만 `ALLOW_EXTERNAL_API=true`로 바꾼다.
키의 존재 검사는 인증 성공을 뜻하지 않는다. `false` 상태에서는 외부 호출을 허용하지 않는다.

```powershell
$settings = Join-Path $installRoot 'settings.env'
& $serverPython -m solo_leveling.interfaces.cli.desktop check --env-file $settings
& $serverPython -m solo_leveling.interfaces.cli.desktop prepare-model --env-file $settings
& $serverPython -m solo_leveling.interfaces.cli.desktop prepare-model --env-file $settings --offline
& $serverPython -m solo_leveling.interfaces.cli.desktop config --env-file $settings
```

- `check`: 설정·설치 패키지·UI 파일 확인. 서버 실행과 같은 검증으로 임베딩 제한 시간,
  업로드 용량, 동시 등록 수, 번역·생성 설정을 검사한다. DB 생성·API 호출·모델 다운로드 없음.
  잘못된 설정이면 서버 실행도 DB 초기화·기존 작업 상태 변경 전에 중단한다.
- `prepare-model`: 동일 설정의 캐시에 기본 임베딩 모델 다운로드·로딩·샘플 계산.
  준비 명령 종료 후 모델은 메모리에 남지 않는다. 기존 색인이 다른 모델이면 별도 준비가 필요하다.
- `config`: Desktop 설정에 병합할 JSON만 출력. 키를 출력하지 않고 호스트 파일을 수정하지 않는다.
- `serve`: Desktop이 호출하는 서버 실행. 시작 시 DB 초기화와 기존 processing 작업의 interrupted 전환이
  있으므로 같은 데이터로 서버를 여러 개 실행하지 않는다.

`config` 출력의 `mcpServers` 항목을 Claude Desktop 개발자 설정에서 여는 설정 파일에 병합한다.
다른 서버 항목을 지우지 않는다. 기본 파일 위치는 Windows에서
`%APPDATA%\Claude\claude_desktop_config.json`이다. 앱 버전에 따라 UI 경로가 다를 수 있다.
Desktop을 재시작한 뒤 도구 목록과 상태 카드를 확인한다. 수동으로 서버를 동시에 켜지 않는다.
표준 출력은 MCP 통신 전용이며 진단은 `DATA_ROOT/diagnostics`에 저장된다.
로그에는 로컬 모듈·파일 경로 및 외부 라이브러리 메시지가 포함될 수 있어 공유 전 확인한다.

C 진단 실행기는 기본적으로 허용된 단계 로그와 최소 시작 정보만 저장한다.
상세 진단이 필요하면 Desktop 설정의 `serve` 인자 목록에 `--collect-raw-logs`를 추가하고
진행 중인 작업이 종료된 뒤 연결을 재시작한다. 이 옵션은 `serve`에서만 지원하며
`config`가 생성하는 기본 설정에는 포함되지 않는다. 상세 모드에서는 원본 stderr·경로·스택에
민감정보가 포함될 수 있다. 진단 후 옵션을 제거하고 재시작한다. 기존 로그는 자동 삭제되지 않는다.
이 옵션은 임베딩 자식 프로세스의 원본 stdout·stderr와 지연 스택 수집에도 적용된다(#85).
옵션이 없으면 `.env`나 프로세스 환경의 상세 수집 설정보다 기본 비수집을 우선한다.
자식 기본 로그에는 `worker_enter`만 남는다. `--detailed-diagnostics`만으로는
자식 원본 로그를 수집하지 않는다. 보관 기간·자동 정리 정책은 #99의 후속 범위다.

## 저장 위치와 기존 사용자

| 대상 | 새 설치 예시 | 업데이트·제거 정책 |
|---|---|---|
| 프로그램·의존성 | `SoloLevelingPaper/venv` | 교체·패키지 제거 대상 |
| 설정·API 키 | `SoloLevelingPaper/settings.env` | 자동 삭제하지 않음 |
| SQLite·Chroma·관리 PDF | `DATA_ROOT` | 보존, 서버 종료 후 함께 백업 |
| 가져올 PDF | `DATA_ROOT/imports` | 사용자가 복사, 자동 삭제하지 않음 |
| 진단 로그 | `DATA_ROOT/diagnostics` | 보존, 용량을 확인하고 명시적으로 정리 |
| 모델 캐시 | `HF_HOME` 또는 기존 HF 캐시 | 보존, 다른 프로그램과 공유 시 삭제 금지 |

기존 `solo-leveling-mcp`와 `python -m solo_leveling.interfaces.mcp`는
기존 `.env`·상대 `DATA_ROOT` 동작을 유지한다. 기존 개발용 scripts 명령도 유지한다.
저장소용 모델 준비 래퍼는 자식 프로세스에도 현재 소스 경로를 전달하므로,
프로젝트 패키지를 설치하지 않았거나 다른 버전이 설치되어 있어도 현재 소스를 우선한다.
새 Desktop 명령으로 전환할 때 **현재 실제 데이터 위치의 절대 경로**를 설정한다.
자동 이관·삭제는 하지 않는다. `MODEL_CACHE_SUBDIR`·`LOG_SUBDIR`만 바꿔서는 실제 캐시·진단 위치가 바뀌지 않는다.
프로세스 환경이 설정 파일보다 우선하므로, 기존 Desktop의 `env`에 남은 DATA_ROOT·키·HF 설정도 확인한다.
설정 파일에는 경로를 완전히 적으며 `%LOCALAPPDATA%` 같은 셸 변수 확장을 기대하지 않는다.

## 업데이트·제거

1. 진행 중 등록이 끝난 뒤 Desktop과 해당 MCP 서버를 종료한다.
2. 설정과 DATA_ROOT 전체(SQLite·Chroma·PDF)를 함께 백업한다.
3. 릴리스의 DB 호환성을 확인하고 새 가상환경에 새 wheel을 설치한다.
4. 기존 DATA_ROOT·캐시를 가리키는 설정으로 check 및 연결을 확인한다.
5. Desktop 실행 경로를 새 환경으로 변경한다. 실패 시 구버전과 호환되는 백업으로 복구한다.
   신버전이 변경한 DB에 구버전을 바로 연결해도 된다고 가정하지 않는다.

제거는 Desktop 서버 항목을 제거하고 프로세스를 종료한 뒤,
해당 가상환경의 `python -m pip uninstall solo-leveling-paper`로 수행한다.
의존성은 자동 제거되지 않는다. 전용 가상환경 삭제는 사용자가 경로를 확인한 뒤 별도로 수행한다.
DB·PDF·설정·캐시는 남는다. 데이터 완전 삭제는 백업 필요 여부·공유 캐시 여부를 확인한 뒤
사용자가 각각 명시적으로 수행한다. 루트 폴더 전체 자동 삭제 명령은 제공하지 않는다.

## 설치 산출물 검증

새 가상환경에 wheel의 `[local]`을 설치한 후 개발 환경에서 실행한다.
검증 대상 환경에 editable 설치나 `--system-site-packages`를 사용하지 않는다.
브랜치를 바꿔 빌드할 때는 이전 `build/`의 파일이 wheel에 남지 않도록 깨끗한
체크아웃에서 빌드한다. 이번 변경에 포함되지 않은 파일이 섞인 wheel을 검증 결과로 사용하지 않는다.

```powershell
$env:SOLO_INSTALLED_PYTHON = 'C:\검증용환경\Scripts\python.exe'
python -m pytest tests/integration/test_installed_distribution.py -q
Remove-Item Env:SOLO_INSTALLED_PYTHON
```

테스트는 임시 폴더와 가짜 키를 사용한다. 저장소 밖 격리 실행, 패키지 import 위치,
설정 점검의 무기록, 실제 stdio 초기화·7개 도구·UI 리소스·안전한 오류·진단 파일을 검사한다.
공개 도구는 `add_paper`, `get_paper_status`, `list_paper_jobs`, `start_learning`,
`ask_paper`, `get_evidence`, `get_learning_dashboard`이며 목록을 정확히 비교한다.
대시보드의 1차 범위와 데이터 기준은 [대시보드 안내](learning-dashboard.md)를 따른다.
`list_paper_jobs`는 설치본에서 입력·출력 스키마, 빈 목록·검색 결과, 잘못된 입력과
커서의 오류 응답 및 오류 후 정상 조회를 확인한다. 실제 작업 목록의 필드·페이지 이동·
검색·읽기 전용 동작은 `tests/unit/test_job_queries.py`와 MCP 단위 테스트에서 별도로 검증한다.
모델 다운로드·실제 API·실제 Desktop 화면은 별도 수동 확인 항목이다.

### #101 검증 기록 — 2026-10-26

- 기준 소스: main `2989696`, 브랜치 `soogwon/issue-101-installed-tool-contract`의
  설치 테스트·문서 변경 적용. 서버 구현 변경은 없다.
- Windows·Python 3.12.14, 저장소 밖 한글·공백 경로에 시스템 패키지를 공유하지 않는
  새 가상환경을 만들고 wheel `[local]`을 설치했다. `pip check` 통과.
- 기존 빌드 디렉터리에 다른 브랜치 파일이 남아 있어, 기준 커밋의 깨끗한 소스 복사본으로
  다시 빌드했다. wheel의 Python 파일 82개가 현재 소스와 일치함을 확인했다.
- `tests/unit/test_job_queries.py`, `tests/unit/mcp/test_server.py`: **84개 통과**.
- `SOLO_INSTALLED_PYTHON`과 저장소 밖의 고유 `--basetemp`를 지정한
  `tests/integration/test_installed_distribution.py`: **1개 통과, 건너뛰기 없음**.
  실제 stdio의 6개 도구 목록, 목록 조회 계약·빈 결과·입력 오류·오류 후 재조회,
  UI 리소스·상태 조회 오류·안전 로그를 확인했다.
- 테스트 전용 DB와 가짜 키만 사용했다. 사용자 데이터 변경·모델 다운로드·생성/번역 API 호출은 없다.
- Linux 추가 검증: Ubuntu 24.04·Python 3.12.15, 커밋 `2658edf`의 깨끗한 체크아웃에서
  wheel을 빌드하고 저장소 밖 한글·공백 경로의 새 가상환경에 설치했다.
  `pip check` 통과, 단위 테스트 **84개 통과**, 설치본 stdio 테스트 **1개 통과, 건너뛰기 없음**.
  [GitHub Actions 실행 결과](https://github.com/soogwon/soloLevelingPaper/actions/runs/40322939094).
- macOS 설치 실행, 실제 Desktop 화면, 실제 논문 등록·질문은 이번 검증에 포함하지 않았다.

### #101 Linux CI와 macOS 수동 확인

`.github/workflows/issue-101-installed-linux.yml`은 #101 브랜치의 코드·테스트·워크플로
변경을 푸시하면 Ubuntu 24.04 / Python 3.12에서 실행한다. 새 가상환경에 wheel `[local]`을
설치하고 `pip check`, 목록 조회·MCP 단위 테스트, 실제 설치본 stdio 테스트를 실행한다.
테스트 XML에 skip이 있으면 실패로 처리한다. Linux 설치 검증에는 CPU용 PyTorch를 사용한다.
기본 브랜치에 병합된 뒤에는 Actions에서 수동으로도 실행할 수 있다.

macOS 담당자는 Python 3.12가 설치된 환경에서 아래 명령을 실행하고, OS 버전·CPU 종류·
커밋 SHA·`pip check` 결과·테스트 결과를 PR에 기록한다. 새 임시 폴더만 사용하며
기존 Desktop 설정·DB를 바꾸지 않는다. 이 절차가 실제 Desktop UI 검증을 대체하지는 않는다.

```bash
# 새 체크아웃을 사용해 기존 빌드 산출물과 실제 사용자 설정을 분리한다.
check_root="$(mktemp -d)/설치 검증"
mkdir -p "$check_root"
git clone --single-branch --branch soogwon/issue-101-installed-tool-contract \
  https://github.com/soogwon/soloLevelingPaper.git "$check_root/source"
cd "$check_root/source"
sw_vers
uname -m
git rev-parse HEAD
python3.12 -m venv "$check_root/venv"
verify_python="$check_root/venv/bin/python"
"$verify_python" -m pip install build pytest
"$verify_python" -m build --wheel
wheel_path="$(find "$PWD/dist" -name '*.whl' -type f)"
"$verify_python" -m pip install "${wheel_path}[local]"
"$verify_python" -m pip check
export SOLO_INSTALLED_PYTHON="$verify_python"
export LOCAL_ONLY=true ALLOW_EXTERNAL_API=false HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=false
"$verify_python" -m pytest tests/unit/test_job_queries.py tests/unit/mcp/test_server.py -q
"$verify_python" -m pytest tests/integration/test_installed_distribution.py \
  -v -rs --basetemp "$check_root/pytest"
```

완료 기준은 단위 테스트 통과와 설치 테스트 **1 passed, skip 없음**이다.
`Too many open files`가 발생하면 OS·파일 제한값(`ulimit -n`)과 실패 지점을 함께 기록한다.
전체 테스트나 사용자 논문 재등록은 이 확인에 필요하지 않다.

### 검증 기록 — 2026-10-20

- Windows·Python 3.12.14, 시스템 패키지를 공유하지 않는 새 가상환경에 wheel `[local]` 설치 성공.
- 최종 wheel 재설치 후 `pip check` 통과.
- 설정 사전 검증·저장소 래퍼의 실제 자식 실행을 포함한 관련 테스트: 39개 통과.
- 보완 후 wheel 재설치와 설치 산출물 테스트를 포함한 전체 회귀 테스트:
  702개 및 하위 테스트 74개 통과. 이번 실행에서는 설치 테스트도 제외하지 않았다.
- 기존 pytest 기본 임시 폴더의 접근 권한 오류가 있어 고유 `--basetemp`를 사용했다.
- 유료 API·사용자 DB·모델 다운로드는 사용하지 않았다.
- 자동 테스트는 실제 Desktop 화면과 실제 모델 로딩을 검증하지 않는다.
  기존 #57의 Desktop 화면 확인을 이번 설치본의 검증으로 간주하지 않는다.

### 사용자 수동 확인 — 2026-10-20

- 저장소용 모델 준비 명령의 일반 실행과 `--offline` 실행이 모두 성공했다고 보고했다.
  제공된 출력은 차원 384, 소요 82.6초였다. 이전 차원 조회 메서드의 FutureWarning은 남아 있다.
- 새 설치본 연결 절차를 진행한 뒤 Desktop 연결 성공과 상태 카드 표시를 보고했다.
  기존 작업의 `get_paper_status` 응답은 `ready`, `index`, `result_available: true`였다.
  논문 재등록·번역·`ask_paper` 호출은 하지 않았다고 보고했다.
- 호스트 설명에는 카드가 없다고 나왔으나 사용자는 실제 카드가 표시됐다고 확인했다.
  카드 표시는 사용자 보고에 근거하며, 이번 확인에서 실행 중인 서버의 패키지 경로를
  별도로 대조하지는 않았다.
- 카드 새로고침과 새 설치본의 전체 질문·번역 흐름은 아직 확인하지 않았다.
