# syntax=docker/dockerfile:1.7
# Multi-arch (linux/arm64 for Apple Silicon, linux/amd64 for WSL2). CPU-only torch comes from the
# PyTorch CPU index via uv.lock markers; the SBERT model and spaCy pipeline are baked in at build time.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/opt/hf-cache \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

ARG INSTALL_OCR=false
ARG INSTALL_LIBREOFFICE=false
RUN set -eux; \
    pkgs=""; \
    if [ "$INSTALL_OCR" = "true" ]; then pkgs="$pkgs tesseract-ocr"; fi; \
    if [ "$INSTALL_LIBREOFFICE" = "true" ]; then pkgs="$pkgs libreoffice-writer-nogui"; fi; \
    if [ -n "$pkgs" ]; then apt-get update && apt-get install -y --no-install-recommends $pkgs && rm -rf /var/lib/apt/lists/*; fi

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_OCR" = "true" ]; then extra="--extra ocr"; else extra=""; fi; \
    uv sync --frozen --no-dev --no-install-project $extra

ARG SBERT_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${SBERT_MODEL}', device='cpu')" \
    && python -c "import spacy; spacy.load('en_core_web_sm')"

COPY . .

ENV SBERT_MODEL=${SBERT_MODEL} \
    STORAGE_DIR=/data \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DEVICE=cpu

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
