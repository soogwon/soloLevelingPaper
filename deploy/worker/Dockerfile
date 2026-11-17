# syntax=docker/dockerfile:1
ARG WORKER_RUNTIME_IMAGE
FROM ${WORKER_RUNTIME_IMAGE} AS artifact
ARG WORKER_RUNTIME_IMAGE
USER root
WORKDIR /opt/worker
ENV WORKER_BASE_REFERENCE=${WORKER_RUNTIME_IMAGE} \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
COPY src/solo_leveling/workers/image_preflight.py ./image_preflight.py
COPY deploy/worker/model-lock.json ./model-lock.json
COPY deploy/worker/MODEL-LICENSE.txt ./licenses/MODEL-LICENSE.txt
RUN --network=none --mount=type=bind,from=worker_model,source=.,target=/input/e8f8c211226b894fcb81acc59f3b34ba3efd5f42,ro \
    python image_preflight.py --artifact-only --prepare-model /input/e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
    && find /opt/models /opt/worker -type d -exec chmod 555 {} + \
    && find /opt/models /opt/worker -type f -exec chmod 444 {} +
USER 65532:65532
ENTRYPOINT ["python", "/opt/worker/image_preflight.py"]
CMD ["--artifact-only"]

# worker 코드만 포장한다. 웹/MCP 자산·환경 파일·논문은 이미지에 넣지 않는다.
FROM artifact AS package
USER root
RUN --network=none --mount=type=bind,source=src/solo_leveling,target=/source,ro python - <<'PYTHON'
from pathlib import Path
import shutil
source = Path('/source')
for path in source.rglob('*.py'):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('WORKER_SOURCE_INVALID')
    output = Path('/app/src/solo_leveling') / path.relative_to(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, output)
for path in Path('/app').rglob('*'):
    path.chmod(0o555 if path.is_dir() else 0o444)
Path('/app').chmod(0o555)
PYTHON
ENV PYTHONPATH=/app/src
USER 65532:65532

# 최종 대상은 사전 준비한 Linux 런타임을 요구한다. 패키지를 즉석 설치하지 않는다.
FROM package AS worker
USER root
COPY --from=worker_runtime /runtime-lock.json /opt/worker/runtime-lock.json
RUN --network=none python /opt/worker/image_preflight.py \
    && python -c "from solo_leveling.workers.processing_composition import create_processing_worker" \
    && chmod -R a-w /opt/worker
USER 65532:65532
CMD []

# Render는 기본 대기로 시작하며 명시한 검증 작업만 한 번 실행한다.
FROM worker AS render
ENTRYPOINT ["python", "-m", "solo_leveling.workers.render_service"]
CMD ["--mode", "standby"]

# 기존 기본 빌드는 사전 검사로 종료한다. Render는 --target render로만 선택한다.
FROM worker AS default
