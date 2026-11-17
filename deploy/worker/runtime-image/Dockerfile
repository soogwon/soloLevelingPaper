# syntax=docker/dockerfile:1
# Linux CPU Python base와 wheel 전체 pin은 운영자가 검토해 명시적으로 공급한다.
ARG PYTHON_RUNTIME_BASE
FROM ${PYTHON_RUNTIME_BASE}
ARG PYTHON_RUNTIME_BASE
USER root
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 PYTHONPATH=/opt/worker-tools/src
COPY src/solo_leveling/workers/image_preflight.py /opt/worker-tools/src/solo_leveling/workers/image_preflight.py
COPY src/solo_leveling/workers/runtime_manifest.py /opt/worker-tools/src/solo_leveling/workers/runtime_manifest.py
RUN --network=none --mount=type=bind,from=worker_dependencies,source=.,target=/runtime-input,ro \
    python -m solo_leveling.workers.runtime_manifest --image-reference "$PYTHON_RUNTIME_BASE" --validate-inputs /runtime-input \
    && python -m pip --isolated install --no-index --find-links=/runtime-input --require-hashes --only-binary=:all: --no-cache-dir --no-compile -r /runtime-input/requirements.lock \
    && python -m pip check \
    && python -m solo_leveling.workers.runtime_manifest --image-reference "$PYTHON_RUNTIME_BASE" > /dev/null \
    && chmod -R a-w /opt/worker-tools
USER 65532:65532
# 실제 만들어진 이미지 digest는 빌드 후 별도 인자로 제공한다. worker 작업을 시작하지 않는다.
ENTRYPOINT ["python", "-m", "solo_leveling.workers.runtime_manifest"]
