FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml LICENSE ./
COPY src/solo_leveling/__init__.py ./src/solo_leveling/
COPY src/solo_leveling/domain/__init__.py src/solo_leveling/domain/annotation.py src/solo_leveling/domain/learning_progress.py ./src/solo_leveling/domain/
COPY src/solo_leveling/interfaces/__init__.py ./src/solo_leveling/interfaces/
COPY src/solo_leveling/interfaces/http/*.py ./src/solo_leveling/interfaces/http/
COPY src/solo_leveling/interfaces/cli/__init__.py src/solo_leveling/interfaces/cli/server_migrations.py ./src/solo_leveling/interfaces/cli/
RUN pip install --no-cache-dir ".[server]"

COPY migrations/server/*.sql ./migrations/server/
COPY web/index.html ./web/
COPY web/css/ ./web/css/
COPY web/js/ ./web/js/
COPY deploy/render-start.sh ./deploy/

USER app

CMD ["./deploy/render-start.sh"]
