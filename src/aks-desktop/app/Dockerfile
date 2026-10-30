# One image for the dashboard/orchestrator, the dataset seeder and the benchmark workers.
FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
ARG PIP_INDEX_URL=""
RUN [ -n "$PIP_INDEX_URL" ] || unset PIP_INDEX_URL; pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
