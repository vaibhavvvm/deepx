# Auto-Dev base image: Python AI / ML / DL / Vision.
# Published as ghcr.io/auto-dev-cli/base-aiml.
# CPU-only PyTorch — the sandbox has no GPU passthrough; install GPU extras
# in your project's own requirements if you need them on the host.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates ripgrep coreutils \
        build-essential gcc g++ gfortran \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
        libhdf5-dev libopenblas-dev \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python tooling
RUN pip install --no-cache-dir uv ruff pytest pytest-cov ipykernel

# Core scientific stack
RUN pip install --no-cache-dir \
        numpy pandas scipy matplotlib seaborn \
        scikit-learn scikit-image \
        tqdm rich pyyaml h5py joblib

# Deep learning: PyTorch CPU (+ vision + audio)
RUN pip install --no-cache-dir \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cpu

# Vision
RUN pip install --no-cache-dir \
        opencv-python-headless pillow imageio

# HuggingFace ecosystem
RUN pip install --no-cache-dir \
        transformers datasets tokenizers accelerate \
        huggingface-hub

# Notebooks + experiment tracking (lightweight)
RUN pip install --no-cache-dir \
        jupyter nbformat nbconvert \
        tensorboard

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
