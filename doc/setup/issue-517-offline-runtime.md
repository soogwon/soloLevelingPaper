# #517 Linux 오프라인 runtime 준비

실제 의존성 버전·운영 서버를 임의로 선택하지 않고, 검토한 base digest와 hash 고정 wheel을 받아
네트워크 없는 RUN 단계에서 설치하는 Dockerfile과 설치 metadata 기록 도구를 제공한다.
모델·PDF·API 키를 이 runtime 이미지에 넣지 않는다. 실제 모델은 기존 worker 이미지 단계에서 별도 추가한다.

## 환경 준비

- Linux Docker/BuildKit. Mac은 Docker의 Linux VM이 필요하며 호스트 macOS Python으로 대체하지 않는다.
- CPU 아키텍처를 먼저 고정한다. ARM에서 만든 lock을 x86 실행 증거로 사용하지 않는다.
- 승인된 Python base의 `name@sha256:digest`.
- `worker_dependencies` 디렉터리: requirements.lock과 해당 플랫폼의 wheel 파일만.
- requirements.lock은 전체 전이 의존성에 정확한 `name==version --hash=sha256:...`를 사용한다.
  직접 URL·index 설정·다른 requirements 포함·환경 marker·소스 배포는 허용하지 않는다.
  Linux/CPU 조합별 lock을 별도로 만든다. 파일명/입력 검사만으로 라이브러리 호환성을 보장하지 않는다.

wheel 확보와 실제 모델 다운로드는 이 도구에서 하지 않는다. 검토한 출처에서 별도 준비한다.

## 빌드와 manifest 기록

```sh
docker buildx build --load \
  --build-arg PYTHON_RUNTIME_BASE="$APPROVED_PYTHON_BASE" \
  --build-context worker_dependencies=/absolute/path/to/approved-wheels \
  -f deploy/worker/runtime-image/Dockerfile -t solo-leveling-worker-runtime .
```

pip는 --isolated/--no-index/--require-hashes/--only-binary를 사용하고 RUN은 --network=none이다.
빌드의 base 이미지·Dockerfile frontend 가져오기는 별도 네트워크 동작이다. 완전 오프라인 실행에는 이 캐시도 미리 준비해야 한다.
wheel은 읽기 전용 named context mount로만 제공하며 최종 이미지에 복사하지 않는다.
pip check와 필수 설치 metadata 검사를 통과해야 빌드된다. 결과는 non-root 기본 실행이다.

생성한 runtime 이미지를 승인한 레지스트리에 게시하고 실제 RepoDigest를 확인하는 단계는 별도 승인 후 수행한다.
**Python base digest와 새 runtime 이미지 digest를 혼동하지 않는다.** 다음에는 새 runtime 이미지의 실제 digest를 넣는다.

```sh
docker run --rm --network=none --read-only \
  "$APPROVED_WORKER_RUNTIME_IMAGE" \
  --image-reference "$APPROVED_WORKER_RUNTIME_IMAGE" > runtime-lock.json
```

runtime-lock은 Linux OS/아키텍처/Python 전체 버전과 설치된 모든 distribution 버전을 기록한다.
입력 image reference가 실제 실행 이미지라는 사실은 Docker 실행/운영자가 보장해야 한다.
manifest만으로 이미지 신원·패키지 취약점·실제 모델 import/추론 성공이 증명되지는 않는다.

이후 기존 `deploy/worker/Dockerfile`에 새 runtime digest, runtime-lock, 고정 모델 context를 제공한다.
bootstrap 준비 문서와 함께 모델 loader·CPU FP32·메모리·시간을 별도로 검증해야 한다.

## 자동 검증과 남은 범위

- 단위: pin/hash 없는 lock, URL/index/중첩 입력, 누락/중복/링크/불필요 파일 거부.
- 단위: Linux·digest·필수 패키지·중복 distribution 검사 및 추가 설치 패키지 보존.
- Linux CI: 실제 라이브러리가 아닌 합성 wheel만 생성해 실제 Docker 오프라인 설치, manifest,
  non-root/입력 미포함과 wheel 변조 시 pip hash 실패를 확인한다. 이미지 push/배포/모델 다운로드/API 호출은 없다.

A: 실제 대상 OS/CPU와 base digest·전체 wheel lock, 레지스트리/리소스 제한 선택.
B: 실제 라이브러리 조합·모델 로딩·FP32·성능과 토큰 상한 검토.
C: 합성 빌드를 실제 모델/서비스 종단 성공으로 표시하지 않음. 환경 선택 후 승인 범위를 정리.

실제 의존성 wheel/모델 파일·운영 환경·레지스트리 배포·DB/R2 자격과 PDF별 전송/비용 승인은 남는다.
#517과 #391은 이번 준비만으로 종료하지 않는다.
