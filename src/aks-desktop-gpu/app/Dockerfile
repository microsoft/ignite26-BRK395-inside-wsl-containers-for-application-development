# CUDA 12.8 and cuDNN 9 match the pinned ONNX Runtime GPU package.
FROM docker.io/nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && apt-get clean \
    && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /app
COPY requirements.txt .
ARG PIP_INDEX_URL=""
RUN [ -n "$PIP_INDEX_URL" ] || unset PIP_INDEX_URL; pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "dashboard:app", "--host", "0.0.0.0", "--port", "8000"]
