FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    M2N_HOST=0.0.0.0 \
    M2N_PORT=8765 \
    M2N_HOSTED=true \
    M2N_DATA_DIR=/data \
    M2N_MODELS_DIR=/models \
    HF_HOME=/models/huggingface \
    TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/cache \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    CMAKE_BUILD_PARALLEL_LEVEL=2

# Build tools also support optional private runtimes installed after deployment.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 libgomp1 build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 meet2notes \
    && useradd --uid 10001 --gid 10001 --no-create-home meet2notes \
    && mkdir -p /app /data /models /cache/tmp \
    && chown -R 10001:10001 /data /models /cache

# apt must use the base image's /tmp before the runtime directories exist.
# Set these only after provisioning so build tools also see valid directories.
ENV TMPDIR=/cache/tmp \
    HOME=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Dependencies and CPU runtime only: no model setup/download during build.
RUN python -m pip install --upgrade pip \
    && python -m pip install 'torch>=2.6,<3' 'torchaudio>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install '.[transcription,ai,nvidia-asr,pyannote-diarization]'

USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=4)); raise SystemExit(r['status'] != 'ok')"
CMD ["meet2notes", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
