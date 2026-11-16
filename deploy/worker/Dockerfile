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
RUN --network=none --mount=type=bind,from=worker_model,source=.,target=/input/e8f8c211226b894fcb81acc59f3b34ba3efd5f42,ro \
    python image_preflight.py --artifact-only --prepare-model /input/e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
    && find /opt/models /opt/worker -type d -exec chmod 555 {} + \
    && find /opt/models /opt/worker -type f -exec chmod 444 {} +
USER 65532:65532
ENTRYPOINT ["python", "/opt/worker/image_preflight.py"]
CMD ["--artifact-only"]

# 최종 대상은 사전 준비한 Linux 런타임을 요구한다. 패키지를 즉석 설치하지 않는다.
FROM artifact AS worker
USER root
COPY --from=worker_runtime /runtime-lock.json /opt/worker/runtime-lock.json
COPY src/solo_leveling/ /app/src/solo_leveling/
ENV PYTHONPATH=/app/src
RUN --network=none python /opt/worker/image_preflight.py \
    && python -c "from solo_leveling.workers.processing_composition import create_processing_worker" \
    && chmod -R a-w /app /opt/worker
USER 65532:65532
CMD []
