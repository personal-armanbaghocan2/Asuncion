# Verification appliance ONLY. Never deploy this instead of Dockerfile.vercel.
# Build context has reviewed source/metadata only, no weights or credentials.
FROM python:3.13.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    PSMS_ENV=test PSMS_INFERENCE_CACHE_ENABLED=0 TESSERACT_CMD=/usr/bin/tesseract \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /app
COPY runtime/deploy/requirements-staging-linux-amd64.lock.txt ./deploy/
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgomp1 tesseract-ocr tesseract-ocr-eng fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --only-binary=:all: --no-deps \
       -r deploy/requirements-staging-linux-amd64.lock.txt && pip check
COPY runtime/ /app/
CMD ["python", "-B", "deploy/model_release_gate.py", "supervise"]
