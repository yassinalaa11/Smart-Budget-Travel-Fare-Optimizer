FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system .
COPY api ./api
COPY dashboard ./dashboard
COPY ingestion ./ingestion
COPY db ./db
COPY transform ./transform

EXPOSE 8000 8501
