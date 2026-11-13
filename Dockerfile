FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY . .
RUN pip install --no-cache-dir ".[server]" && chown -R app:app /app

USER app

CMD ["./deploy/render-start.sh"]
