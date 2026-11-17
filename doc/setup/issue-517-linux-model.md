# #517 실제 Linux 이미지·모델 검증

## 실행 범위

`python scripts/verification/linux_worker.py --work-dir /absolute/new-directory`로 실행한다.
Docker가 있는 Linux 또는 Mac에서 가능하며, Mac에서도 wheel 선택은 linux/amd64 Python 컨테이너에서 한다.
패키지/모델 다운로드는 온라인 준비 단계다. PyTorch CPU 공식 index와 PyPI를 사용하고 직접 의존성을 pin한다.
해석된 전체 wheel 버전과 SHA-256을 requirements.lock으로 기록한 뒤 오프라인 hash 필수 설치를 수행한다.
전이 의존성 lock은 실행 시 생성되므로 후속 실행 버전이 달라질 수 있다. 운영에는 검토한 결과 lock을 고정해야 한다.

Hugging Face 고정 revision의 파일 크기/해시를 확인하며 모델/패키지를 저장소에 커밋하지 않는다.
Python base tag는 실행 시 실제 digest로 고정하고 결과에 남긴다. 운영 승인 base를 정한 것은 아니다.

## 이미지와 격리

- runtime 이미지는 digest 확인을 위해 CI/로컬 호스트 loopback registry에만 push한다.
  외부 레지스트리 게시가 아니며 registry는 finally에서 삭제한다.
- 실제 render target을 빌드하고 amd64·non-root·runtime/model 지문을 확인한다.
- 추론 컨테이너는 network=none, read-only, 2 CPU, RAM 4GiB(추가 swap 없음), pids 256,
  cap-drop ALL/no-new-privileges 및 제한된 /tmp를 사용한다.
- 합성 PDF 2페이지 파싱, 수동 합성 번역 두 문장의 CPU/FP32/L2 384차원 임베딩을 두 번 실행한다.
  같은 환경의 반복 결과·finite/norm/차원·artifact 불일치 거부·CUDA 미포함을 검사한다.
- Render 기본 대기에서 작업을 시작하지 않고 SIGTERM 후 종료 코드 0으로 끝나는지 확인한다.
- 실제 PDF/R2/DB/번역 provider/Render는 연결하지 않는다. 유료 API 호출은 없다.

## 증거

reports에는 requirements.lock, runtime-lock.json, linux-result.json, images.json, render-standby.txt만 남긴다.
GitHub Actions는 이 결과만 7일 보관한다. 모델/wheel/이미지는 업로드하지 않는다.
image ID는 로컬 content ID이고 외부 배포 가능한 RepoDigest가 아니다. localhost runtime digest도 외부에서 가져올 수 없다.
Linux CI 통과도 Render 배포·운영 용량·품질 검증을 대신하지 않는다. Mac amd64 에뮬레이션 시간은 별도로 구분한다.

## 남은 준비

실측 성공 후 결과를 기록한다. 외부 레지스트리 게시/보존과 운영용 digest·전체 lock 선정,
격리 DB/R2/동의/예산/작업과 PDF·payload·호출 상한 승인이 남는다.
Render 서비스 생성/Deploy 전에 사용자에게 사양·요금과 검증 후 중지 계획을 알린다.
이번 단계에서는 유료 서비스 생성·결제를 하지 않는다. #517/#391은 열어둔다.
