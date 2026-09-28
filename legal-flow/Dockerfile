FROM python:3.11-slim

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY frontend ./frontend
COPY docker-entrypoint.sh ./

RUN pip install --no-cache-dir . && chmod +x docker-entrypoint.sh

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
