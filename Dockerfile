FROM python:3.12-slim

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY sql ./sql

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PERSISTENCE_WAL_PATH=/tmp/userchat-wal.json

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
