# 팀원용 임베딩 모델 준비

프로젝트 루트의 PowerShell에서 MCP와 같은 사용자·가상환경으로 실행한다.
가상환경 생성 예: `py -3.12 -m venv .venv` (Python 3.12가 설치된 경우).

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[local]"
.\.venv\Scripts\python.exe scripts/prepare_embedding_model.py
```

서비스의 DEFAULT_MODEL_NAME을 사용한다. 최초 실행은 모델 제공 저장소에 접근하여
모델 파일을 다운로드하고 Hugging Face 캐시에 저장한다. 인터넷·디스크 공간이 필요하다.
OpenAI 호출이나 API 키는 필요 없으며 원문·질문·DB·색인은 읽거나 변경하지 않는다.
모델 로딩 후 고정 한국어 샘플의 벡터 차원과 유한한 수치인지 검증한다.
품질·검색 정확도를 평가하는 명령은 아니다.

다음 실행에서 로컬 캐시만으로 준비 가능한지 확인:

```powershell
.\.venv\Scripts\python.exe scripts/prepare_embedding_model.py --offline
```

기본 제한 시간은 600초다. 느린 다운로드 환경에서는 `--timeout 1200` 등으로 늘릴 수 있다.
초과 시 준비용 프로세스만 종료하며 기존 MCP 프로세스는 건드리지 않는다.
중단 시 일부 캐시 파일이 남을 수 있다. 자동 삭제하지 않으며 다시 실행하여 확인한다.
종료 코드 0은 준비 성공, 1은 준비 실패, 124는 시간 초과다.

기본 캐시는 Windows에서 보통 `%USERPROFILE%\.cache\huggingface\hub`다.
HF_HOME·HF_HUB_CACHE 등 캐시 관련 환경을 변경했다면 MCP에도 같은 설정을 사용해야 한다.
이 명령은 .env를 자동 로딩하지 않는다. 프로세스 환경 변수를 상속한다.
`--model`로 다른 모델을 준비할 수 있으나 서비스 설정·기존 색인은 자동 변경하지 않는다.
기존 색인 검색에는 저장된 임베딩 모델과 같은 모델이 필요하다.

준비 프로세스 종료 후 메모리에 로드된 모델은 유지되지 않는다. 디스크 캐시가 남으므로
MCP는 모델을 다시 메모리에 로딩해야 하며, 온라인 실행은 원격 확인을 할 수도 있다.
이 명령은 MCP 선행 라이브러리 import 우회 설정을 대체하지 않는다.
외부 라이브러리 자체 메시지가 콘솔에 출력될 수 있으므로 전체 로그 공유 전 확인한다.
