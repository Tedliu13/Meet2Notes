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
    && CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_CPU_ALL_VARIANTS=OFF \
       -DGGML_SSE42=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_BMI2=OFF \
       -DGGML_FMA=OFF -DGGML_F16C=OFF -DGGML_AVX_VNNI=OFF \
       -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF \
       -DGGML_AVX512_BF16=OFF -DGGML_AMX_TILE=OFF -DGGML_AMX_INT8=OFF \
       -DGGML_AMX_BF16=OFF -DGGML_LLAMAFILE=OFF" \
       python -m pip install --no-binary=llama-cpp-python \
       '.[transcription,ai,nvidia-asr,pyannote-diarization]'

# VM CPU baseline: do not inherit the build host's ISA or a prebuilt native wheel.
RUN python - <<'PY'
import re
from importlib.metadata import version
import llama_cpp

info = llama_cpp.llama_print_system_info().decode('utf-8', errors='replace')
print('Portable llama-cpp-python ' + version('llama-cpp-python') + ': ' + info, flush=True)
assert not re.search(r'\b(?:AVX\w*|FMA|F16C|BMI2|SSE42|AMX\w*)\s*=\s*1\b', info), info
PY

# Fail the Coolify build if the installed decoder API is incompatible.
# This uses generated audio only and never loads or downloads a model.
RUN python - <<'PY'
import io
import wave
from importlib.metadata import version
from faster_whisper.audio import decode_audio

print('Transcription runtime: av=' + version('av') + ', faster-whisper=' + version('faster-whisper'), flush=True)
audio_file = io.BytesIO()
with wave.open(audio_file, 'wb') as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes(b'\x00\x00' * 16000)
audio_file.seek(0)
decoded = decode_audio(audio_file, sampling_rate=16000)
assert decoded.shape == (16000,), decoded.shape
assert str(decoded.dtype) == 'float32', decoded.dtype
print('Faster Whisper WAV decode passed', flush=True)
PY

USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=4)); raise SystemExit(r['status'] != 'ok')"
CMD ["meet2notes", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
