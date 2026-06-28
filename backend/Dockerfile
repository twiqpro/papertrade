# Build context must be the repo root (`.`). Render: Dockerfile Path = backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV ROOT_PATH=/options-backtest

COPY backend/requirements.txt .
COPY backtester/requirements.txt ./backtester-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r backtester-requirements.txt

COPY backend/app ./app
COPY backtester ./backtester
COPY frontend ./frontend

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.combined:root --host 0.0.0.0 --port ${PORT:-8000}"]
