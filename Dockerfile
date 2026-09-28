# syntax=docker/dockerfile:1
# Initialize device type args
# use build args in the docker build command with --build-arg="BUILDARG=true"
ARG USE_CUDA=false
ARG USE_OLLAMA=false
ARG USE_SLIM=false
ARG USE_PERMISSION_HARDENING=false
# Tested with cu117 for CUDA 11 and cu121 for CUDA 12 (default)
ARG USE_CUDA_VER=cu128
# any sentence transformer model; models to use can be found at https://huggingface.co/models?library=sentence-transformers
# Leaderboard: https://huggingface.co/spaces/mteb/leaderboard 
# for better performance and multilangauge support use "intfloat/multilingual-e5-large" (~2.5GB) or "intfloat/multilingual-e5-base" (~1.5GB)
# IMPORTANT: If you change the embedding model (sentence-transformers/all-MiniLM-L6-v2) and vice versa, you aren't able to use RAG Chat with your previous documents loaded in the WebUI! You need to re-embed them.
ARG USE_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ARG USE_RERANKING_MODEL=""
ARG USE_AUXILIARY_EMBEDDING_MODEL=TaylorAI/bge-micro-v2

# Tiktoken encoding name; models to use can be found at https://huggingface.co/models?library=tiktoken
ARG USE_TIKTOKEN_ENCODING_NAME="cl100k_base"

ARG BUILD_HASH=dev-build
# Override at your own risk - non-root configurations are untested
ARG UID=0
ARG GID=0

######## Integration Go Binaries Builder ########
FROM golang:1.23-bookworm AS integration-go-builder
WORKDIR /src
COPY plandex /src/plandex
COPY scripts/integration /src/scripts/integration
COPY integration /src/integration

# Build Plandex CLI
RUN cd /src/plandex/app/cli && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /opt/integration/bin/plandex .

# Build Plandex Server
RUN cd /src/plandex/app/server && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /opt/integration/bin/plandex-server .

# Build GitHub MCP Server
ARG GITHUB_MCP_VERSION=v1.12.2
ARG GITHUB_MCP_REVISION=85598ba6e1256f7ebf4867b95d63b833c4549264
RUN git clone --quiet https://github.com/github/github-mcp-server.git /tmp/github-mcp && \
    git -C /tmp/github-mcp checkout --quiet --detach "${GITHUB_MCP_REVISION}" && \
    cd /tmp/github-mcp && \
    CGO_ENABLED=0 go build -trimpath \
    -ldflags="-s -w -X main.version=${GITHUB_MCP_VERSION} -X main.commit=${GITHUB_MCP_REVISION} -X main.date=deployment-build" \
    -o /opt/integration/bin/github-mcp-server ./cmd/github-mcp-server

######## Integration Tiktoken Cache Builder ########
FROM python:3.11-slim-bookworm AS plandex-tokenizer
ARG TIKTOKEN_O200K_FETCH_URL="https://raw.githubusercontent.com/rmusser01/tldw_chatbook/b0dadf19414f5f8faf69b854d8e59007d275083e/tldw_chatbook/assets/tiktoken_cache/fb374d419588a4632f3f557e76b4b70aebbca790"
ENV TIKTOKEN_CACHE_DIR=/opt/integration/tiktoken-cache
WORKDIR /src
RUN mkdir -p backend/open_webui/control_plane && touch backend/open_webui/__init__.py backend/open_webui/control_plane/__init__.py
COPY backend/open_webui/control_plane/plandex_tokenizer.py /src/backend/open_webui/control_plane/plandex_tokenizer.py
COPY scripts/integration/fetch-tiktoken-artifacts.py scripts/integration/plandex_tokenizer_runtime.py scripts/integration/
ENV PYTHONPATH=/src/backend
RUN TIKTOKEN_O200K_FETCH_URL="$TIKTOKEN_O200K_FETCH_URL" \
    python scripts/integration/fetch-tiktoken-artifacts.py && \
    chmod 0444 "$TIKTOKEN_CACHE_DIR/fb374d419588a4632f3f557e76b4b70aebbca790" && \
    chmod 0555 "$TIKTOKEN_CACHE_DIR"

######## WebUI frontend ########
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
ARG BUILD_HASH
ARG USE_SLIM
ARG UID
ARG GID

# Set Node.js options (heap limit Allocation failed - JavaScript heap out of memory)
ENV NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /app

# to store git revision in build
RUN apk add --no-cache git

COPY package.json package-lock.json ./
RUN npm ci --force

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
RUN npm run build && \
    if [ "$USE_SLIM" = "true" ]; then find build -type f -name '*.map' -delete; fi

# Prepare backend ownership before the final copy so static assets occupy one layer.
# Group 0 write access lets arbitrary OpenShift UIDs update these assets at startup.
RUN chown -R $UID:$GID /app/backend && \
    chgrp -R 0 /app/backend/open_webui/static && \
    chmod -R g=u /app/backend/open_webui/static

######## WebUI backend ########
FROM python:3.11-slim-bookworm AS base

# Use args
ARG USE_CUDA
ARG USE_OLLAMA
ARG USE_CUDA_VER
ARG USE_SLIM
ARG USE_PERMISSION_HARDENING
ARG USE_EMBEDDING_MODEL
ARG USE_RERANKING_MODEL
ARG USE_AUXILIARY_EMBEDDING_MODEL
ARG UID
ARG GID

# Python settings
ENV PYTHONUNBUFFERED=1

## Basis ##
ENV ENV=prod \
    PORT=8080 \
    # pass build args to the build
    USE_OLLAMA_DOCKER=${USE_OLLAMA} \
    USE_CUDA_DOCKER=${USE_CUDA} \
    USE_SLIM_DOCKER=${USE_SLIM} \
    USE_CUDA_DOCKER_VER=${USE_CUDA_VER} \
    USE_EMBEDDING_MODEL_DOCKER=${USE_EMBEDDING_MODEL} \
    USE_RERANKING_MODEL_DOCKER=${USE_RERANKING_MODEL} \
    USE_AUXILIARY_EMBEDDING_MODEL_DOCKER=${USE_AUXILIARY_EMBEDDING_MODEL}

## Basis URL Config ##
ENV OLLAMA_BASE_URL="/ollama" \
    OPENAI_API_BASE_URL=""

## API Key and Security Config ##
ENV OPENAI_API_KEY="" \
    WEBUI_SECRET_KEY="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false

#### Other models #########################################################
## whisper TTS model settings ##
ENV WHISPER_MODEL="base" \
    WHISPER_MODEL_DIR="/app/backend/data/cache/whisper/models"

## RAG Embedding model settings ##
ENV RAG_EMBEDDING_MODEL="$USE_EMBEDDING_MODEL_DOCKER" \
    RAG_RERANKING_MODEL="$USE_RERANKING_MODEL_DOCKER" \
    AUXILIARY_EMBEDDING_MODEL="$USE_AUXILIARY_EMBEDDING_MODEL_DOCKER" \
    SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models"

## Tiktoken model settings ##
ENV TIKTOKEN_ENCODING_NAME="cl100k_base" \
    TIKTOKEN_CACHE_DIR="/app/backend/data/cache/tiktoken"

## Hugging Face download cache ##
ENV HF_HOME="/app/backend/data/cache/embedding/models"

## Torch Extensions ##
# ENV TORCH_EXTENSIONS_DIR="/.cache/torch_extensions"

#### Other models ##########################################################

WORKDIR /app/backend

ENV HOME=/root
# Create user and group if not root
RUN if [ $UID -ne 0 ]; then \
    if [ $GID -ne 0 ]; then \
    addgroup --gid $GID app; \
    fi; \
    adduser --uid $UID --gid $GID --home $HOME --disabled-password --no-create-home app; \
    fi

RUN mkdir -p $HOME/.cache/chroma
RUN echo -n 00000000-0000-0000-0000-000000000000 > $HOME/.cache/chroma/telemetry_user_id

# Make sure the user has access to the app and root directory
RUN chown -R $UID:$GID /app $HOME

# Slim cannot bundle a local model server or GPU runtime.
RUN if [ "$USE_SLIM" = "true" ] && { [ "$USE_CUDA" = "true" ] || [ "$USE_OLLAMA" = "true" ]; }; then \
    echo "USE_SLIM cannot be combined with USE_CUDA or USE_OLLAMA" >&2; exit 1; fi

# Keep the slim runtime free of local document/audio processing tools.
# Git-based tool requirements require the standard image.
# Install PostgreSQL server runtime for single-container deployment
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl jq ca-certificates procps postgresql postgresql-contrib \
    && if [ "$USE_SLIM" != "true" ]; then \
    apt-get install -y --no-install-recommends \
    git build-essential pandoc gcc libmariadb-dev ffmpeg libsm6 libxext6; \
    fi && if [ "$USE_OLLAMA" = "true" ]; then \
    apt-get install -y --no-install-recommends zstd; \
    fi && rm -rf /var/lib/apt/lists/*

# install python dependencies including integration requirements
COPY --chown=$UID:$GID ./backend/requirements*.txt ./
COPY --chown=$UID:$GID ./integration/requirements*.txt /tmp/integration/

# Set UV_LINK_MODE to copy to prevent 0-byte file corruption in QEMU arm64 cross-builds
ENV UV_LINK_MODE=copy

RUN --mount=from=ghcr.io/astral-sh/uv:0.12.10,source=/uv,target=/bin/uv \
    set -e; \
    if [ "$USE_SLIM" = "true" ]; then \
    uv pip install --system -r requirements-slim.txt --no-cache-dir; \
    elif [ "$USE_CUDA" = "true" ]; then \
    # If you use CUDA the whisper and embedding model will be downloaded on first use
    # fix: pin torch<=2.9.1 - torch 2.10.0 aarch64 wheels cause SIGILL on ARM devices (RPi 4 Cortex-A72) #21349
    pip3 install 'torch<=2.9.1' torchvision torchaudio --index-url https://download.pytorch.org/whl/$USE_CUDA_DOCKER_VER --no-cache-dir; \
    uv pip install --system -r requirements.txt --no-cache-dir; \
    uv pip install --system -r /tmp/integration/requirements.txt -r /tmp/integration/requirements-graphify.txt --no-cache-dir; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')"; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2'), device='cpu')"; \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    else \
    pip3 install 'torch<=2.9.1' torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --no-cache-dir; \
    uv pip install --system -r requirements.txt --no-cache-dir; \
    uv pip install --system -r /tmp/integration/requirements.txt -r /tmp/integration/requirements-graphify.txt --no-cache-dir; \
    if [ "$USE_SLIM" != "true" ]; then \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')" || true; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2'), device='cpu')" || true; \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])" || true; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])" || true; \
    fi; \
    fi; \
    mkdir -p /app/backend/data; chown -R $UID:$GID /app/backend/data/; \
    if [ -d /app/backend/data/cache ]; then chmod -R a+rX /app/backend/data/cache; fi; \
    rm -rf /var/lib/apt/lists/*;

# Optional: PPTX parsing through unstructured may need spaCy's English model.
# Keep this out of the default image to avoid the extra image bloat; deployments
# with read-only site-packages can uncomment it and bake the model in.
# RUN python -m spacy download en_core_web_sm

# Install Ollama if requested
RUN if [ "$USE_OLLAMA" = "true" ]; then \
    date +%s > /tmp/ollama_build_hash && \
    echo "Cache broken at timestamp: `cat /tmp/ollama_build_hash`" && \
    curl -fsSL https://ollama.com/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# copy embedding weight from build
# RUN mkdir -p /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2
# COPY --from=build /app/onnx /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx

# copy built frontend files
COPY --chown=$UID:$GID --from=build /app/build /app/build
COPY --chown=$UID:$GID --from=build /app/CHANGELOG.md /app/CHANGELOG.md
COPY --chown=$UID:$GID --from=build /app/package.json /app/package.json

# copy backend files with the ownership and static permissions prepared above
COPY --from=build /app/backend .

# Copy compiled Go integration binaries from builder
COPY --from=integration-go-builder /opt/integration/bin /opt/integration/bin

# Copy pre-fetched tiktoken cache
COPY --from=plandex-tokenizer /opt/integration/tiktoken-cache /opt/integration/tiktoken-cache

# Copy integration components, scripts, and AGENTS.md
COPY integration /app/integration
COPY scripts /app/scripts
COPY AGENTS.md /app/AGENTS.md

# Fetch Needle runtime artifacts and perform offline preflight during build
RUN --mount=type=secret,id=hf_token,required=false \
    if [ -f /run/secrets/hf_token ]; then export HF_TOKEN="$(cat /run/secrets/hf_token)"; fi && \
    python /app/scripts/integration/fetch-needle-artifacts.py && \
    HF_HUB_OFFLINE=1 python /app/scripts/integration/needle-preflight.py

# Build-time deterministic artifact and binary verification
RUN test -x /opt/integration/bin/plandex && \
    test -x /opt/integration/bin/plandex-server && \
    test -x /opt/integration/bin/github-mcp-server && \
    test -f /opt/integration/tiktoken-cache/fb374d419588a4632f3f557e76b4b70aebbca790 && \
    test -f /app/integration/deployment-artifacts.json && \
    test -x /app/scripts/integration/deployment-smoke.sh && \
    echo "=== BUILD PROOF: All deployment artifacts and binaries successfully verified ==="

EXPOSE 7860 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-7860}/health | jq -ne 'input.status == true' || exit 1

# Minimal, atomic permission hardening for OpenShift (arbitrary UID):
# - Group 0 owns /app and /root
# - Directories are group-writable and have SGID so new files inherit GID 0
RUN if [ "$USE_PERMISSION_HARDENING" = "true" ]; then \
    set -eux; \
    chgrp -R 0 /app /root || true; \
    chmod -R g+rwX /app /root || true; \
    find /app -type d -exec chmod g+s {} + || true; \
    find /root -type d -exec chmod g+s {} + || true; \
    fi

USER $UID:$GID

ARG BUILD_HASH
ENV WEBUI_BUILD_VERSION=${BUILD_HASH}
ENV DOCKER=true

CMD [ "/app/scripts/integration/supervisor.sh" ]
