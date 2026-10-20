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

```powershell
$env:SOLO_INSTALLED_PYTHON = 'C:\검증용환경\Scripts\python.exe'
python -m pytest tests/integration/test_installed_distribution.py -q
Remove-Item Env:SOLO_INSTALLED_PYTHON
```

테스트는 임시 폴더와 가짜 키를 사용한다. 저장소 밖 격리 실행, 패키지 import 위치,
설정 점검의 무기록, 실제 stdio 초기화·5개 도구·UI 리소스·안전한 오류·진단 파일을 검사한다.
모델 다운로드·실제 API·실제 Desktop 화면은 별도 수동 확인 항목이다.

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
