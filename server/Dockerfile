# vdi-dictate ASR server.
# Default: onnx-asr (NVIDIA Parakeet) on GPU via onnxruntime-gpu.
#
# Build args (set by docker-compose / run-server.ps1):
#   ORT_PACKAGE   onnxruntime-gpu (default) | onnxruntime   (CPU-only build)
#   INSTALL_HF    false (default) | true    (adds torch + transformers)
#   CUDA_IMAGE    base image

ARG CUDA_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
FROM ${CUDA_IMAGE}

ARG ORT_PACKAGE=onnxruntime-gpu
ARG INSTALL_HF=false

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip libsndfile1 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /app

COPY server/requirements.txt server/requirements-hf.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt "${ORT_PACKAGE}" \
    && if [ "$INSTALL_HF" = "true" ]; then python -m pip install -r requirements-hf.txt; fi

COPY server/engines.py server/app.py ./

EXPOSE 9000
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=40 \
    CMD curl -fsS http://localhost:9000/health || exit 1

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
