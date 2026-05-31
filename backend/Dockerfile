# Backend image. Build context is the repo root so the `backend` package keeps
# its import path (`backend.api.main:app`). See docker-compose.yml.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend

EXPOSE 8000
# Shell form so Cloud Run's injected $PORT (8080) is honored; defaults to 8000
# for local Compose (which maps 8000:8000 and sets no PORT).
CMD exec uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
