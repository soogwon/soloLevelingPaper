# #517 worker 이미지 준비

이 변경은 8번의 **이미지 입력·빌드 경계 준비**다. 기존 Render 웹 Dockerfile과 분리한다.
이미지 기본 명령은 오프라인 사전 검사 후 종료하며 큐를 가져오거나 서버/API에 접속하지 않는다.
실제 worker loop·설정 snapshot·저장 번역 재사용·staging·게시 연결은 후속이다.

## 입력

- `deploy/worker/model-lock.json`: #391 승인 preflight의 모델 revision, 파일별 크기/SHA-256,
  기존 adapter와 동일한 파일 집합 digest. 실제 모델 파일은 저장소에 넣지 않는다.
- `worker_model`: 해당 snapshot의 **실제 파일만** 담은 별도 로컬 빌드 context. HF 캐시 링크는
  사전에 일반 파일로 내보내야 한다. 파일·디렉터리 링크, 누락·변조·추가 파일은 거부한다.
  모델 다운로드는 이 Dockerfile에서 하지 않는다. 개인 PDF·키·환경 파일을 context에 넣지 않는다.
- `WORKER_RUNTIME_IMAGE`: 필요한 패키지가 사전에 설치된 Linux 이미지의 `name@sha256:...` 참조.
  mutable tag는 거부한다. Windows preflight 버전을 Linux 의존성 lock으로 사용하지 않는다.
- `worker_runtime/runtime-lock.json`: 해당 이미지에서 검토·기록한 아래 형태의 manifest.
  필수 패키지와 추가 기록 패키지의 설치 metadata를 정확히 대조한다. 버전을 즉석 설치하지 않는다.

```json
{
  "schema_version": 1,
  "base_image": "검토한 Linux 이미지의 digest 참조",
  "system": "Linux",
  "machine": "실측한 platform.machine 값",
  "python": "실측한 Python 전체 버전",
  "packages": {
    "torch": "검토한 버전", "sentence-transformers": "검토한 버전",
    "transformers": "검토한 버전", "tokenizers": "검토한 버전",
    "numpy": "검토한 버전", "pdfplumber": "검토한 버전",
    "requests": "검토한 버전", "psycopg": "검토한 버전",
    "psycopg-pool": "검토한 버전", "boto3": "검토한 버전"
  }
}
```

위 예시는 실행 가능한 운영 lock이 아니다. Linux 의존성 전체 고정/취약점 검토·실제 loader
호환·FP32 검증을 마친 runtime image와 manifest가 아직 필요하다. metadata 일치만으로
라이브러리 호환이나 실제 모델 실행 성공을 보장하지 않는다.

## 준비 후 빌드

[Docker named context](https://docs.docker.com/build/concepts/context/)로 모델과 runtime
manifest를 별도 공급한다. repo 전체를 모델 context로 쓰지 않는다.

```sh
docker buildx build --load --target worker \
  --build-arg WORKER_RUNTIME_IMAGE="$APPROVED_WORKER_IMAGE" \
  --build-context worker_model=/absolute/path/to/exported-snapshot \
  --build-context worker_runtime=/absolute/path/to/runtime-manifest \
  -f deploy/worker/Dockerfile -t solo-leveling-worker-inputs .
docker run --rm --network=none --read-only solo-leveling-worker-inputs
```

검사는 `WORKER_IMAGE_INPUTS_VERIFIED` 또는 고정 실패 코드만 출력한다. 이것은 배포나 논문
처리 완료를 뜻하지 않는다. 최종 대상은 runtime 검사를 통과하고 worker composition import까지
성공해야 빌드된다. 이후 운영 실행기가 생겨도 non-root·읽기 전용 모델/rootfs를 유지해야 한다.

모델은 읽기 전용 build mount에서 확인한 파일만 복사하고 복사 후 다시 검증한다. 모델과 검사
manifest는 root 소유/쓰기 불가이며 실행 UID는 65532다. 운영자가 root나 다른 mount/entrypoint로
이를 바꾸는 경우까지 막는 sandbox는 아니다. 런타임 모델 검사/동의 검사를 대체하지 않는다.

## 자동 검증과 제한

- 단위: manifest 동일성, 파일 변조·누락·추가·경로 이탈·링크·중복 키 거부,
  runtime digest/OS/CPU/Python/필수 패키지 버전 불일치.
- Linux CI: 임시 context에서만 합성 모델과 합성 lock으로 교체해 `package` 대상을 실제 빌드한다.
  정상 검사, Python 소스만 포함(환경/PDF/HTML 제외), non-root, 모델/manifest 쓰기 거부, runtime 누락 시 최종 worker 빌드 실패,
  모델 변조 시 빌드 실패를 확인한다. 이미지 push·배포·실제 모델 다운로드/추론·AI 호출은 없다.
- `artifact`/`package` 대상 통과는 최종 `worker` 대상 통과가 아니다. CI용 Python base digest는 운영 승인값이 아니다.

A: Linux runtime digest/전체 의존성·운영 자원과 worker loop 연결이 남는다.
B: 실제 artifact/loader/tokenizer/pooling/FP32·RSS/시간 검증 및 #391 ONNX 비교가 남는다.
C: 입력 검증 성공과 작업 처리/게시 성공을 분리한다. #517/#391을 자동 종료하지 않는다.
