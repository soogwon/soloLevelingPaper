FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml LICENSE ./
COPY src/solo_leveling/__init__.py ./src/solo_leveling/
COPY src/solo_leveling/domain/__init__.py src/solo_leveling/domain/annotation.py src/solo_leveling/domain/learning_progress.py src/solo_leveling/domain/knowledge_graph.py ./src/solo_leveling/domain/
COPY src/solo_leveling/domain/deletion_policy.py ./src/solo_leveling/domain/
COPY src/solo_leveling/knowledge/ ./src/solo_leveling/knowledge/
COPY src/solo_leveling/domain/models.py src/solo_leveling/domain/evidence_qa.py ./src/solo_leveling/domain/
COPY src/solo_leveling/application/__init__.py ./src/solo_leveling/application/
COPY src/solo_leveling/application/remote_replay.py src/solo_leveling/application/remote_execution_policy.py src/solo_leveling/application/provider_dispatch.py ./src/solo_leveling/application/
COPY src/solo_leveling/application/evidence_qa/remote_input.py src/solo_leveling/application/evidence_qa/remote_question_input.py ./src/solo_leveling/application/evidence_qa/
COPY src/solo_leveling/application/evidence_qa/validators.py src/solo_leveling/application/evidence_qa/page_completion.py src/solo_leveling/application/evidence_qa/fragments.py ./src/solo_leveling/application/evidence_qa/
COPY src/solo_leveling/interfaces/mcp/__init__.py src/solo_leveling/interfaces/mcp/remote_product.py src/solo_leveling/interfaces/mcp/remote_recovery.py src/solo_leveling/interfaces/mcp/remote_questions.py src/solo_leveling/interfaces/mcp/remote_annotations.py src/solo_leveling/interfaces/mcp/remote_annotation_cards.py ./src/solo_leveling/interfaces/mcp/
COPY src/solo_leveling/application/evidence_qa/__init__.py src/solo_leveling/application/evidence_qa/remote_contract.py ./src/solo_leveling/application/evidence_qa/
COPY src/solo_leveling/application/learning_path/__init__.py src/solo_leveling/application/learning_path/views.py ./src/solo_leveling/application/learning_path/
COPY src/solo_leveling/infrastructure/__init__.py ./src/solo_leveling/infrastructure/
COPY src/solo_leveling/infrastructure/storage/__init__.py src/solo_leveling/infrastructure/storage/remote_deletion_backend.py ./src/solo_leveling/infrastructure/storage/
COPY src/solo_leveling/interfaces/__init__.py ./src/solo_leveling/interfaces/
COPY src/solo_leveling/interfaces/http/*.py ./src/solo_leveling/interfaces/http/
COPY src/solo_leveling/interfaces/cli/__init__.py src/solo_leveling/interfaces/cli/server_migrations.py src/solo_leveling/interfaces/cli/remote_deletion_cleanup.py src/solo_leveling/interfaces/cli/remote_upload_cleanup.py ./src/solo_leveling/interfaces/cli/
COPY src/solo_leveling/interfaces/mcp/assets/remote-annotation-recovery.html src/solo_leveling/interfaces/mcp/assets/remote-annotation-recovery.LICENSE.txt ./src/solo_leveling/interfaces/mcp/assets/
COPY src/solo_leveling/interfaces/mcp/assets/remote-annotation-compose.html src/solo_leveling/interfaces/mcp/assets/remote-annotation-compose.LICENSE.txt ./src/solo_leveling/interfaces/mcp/assets/
RUN pip install --no-cache-dir ".[server]"

COPY migrations/server/*.sql ./migrations/server/
COPY web/index.html ./web/
COPY web/css/ ./web/css/
COPY web/js/ ./web/js/
COPY web/landing/ ./web/landing/
COPY deploy/render-start.sh ./deploy/
COPY deploy/cleanup/run.py ./deploy/cleanup/run.py

USER app

CMD ["./deploy/render-start.sh"]
