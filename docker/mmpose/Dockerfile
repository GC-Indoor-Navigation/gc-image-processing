# syntax=docker/dockerfile:1

ARG BASE_IMAGE=nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    CUDA_HOME=/usr/local/cuda \
    FORCE_CUDA=1 \
    MAX_JOBS=1 \
    TORCH_CUDA_ARCH_LIST=8.6

WORKDIR /workspace/gc-image-processing

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        ninja-build \
        python3-dev \
        python3-pip \
        python3-venv \
        wget \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3 /usr/local/bin/pip \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade "pip<25" "setuptools==69.5.1" wheel

COPY requirements.txt /tmp/requirements-processing.txt
COPY docker/mmpose/requirements-mmpose.txt /tmp/requirements-mmpose.txt

RUN python -m pip install -r /tmp/requirements-processing.txt

RUN python -m pip install \
        torch==2.1.0 \
        torchvision==0.16.0 \
        torchaudio==2.1.0 \
        --index-url https://download.pytorch.org/whl/cu121

RUN python -m pip install -r /tmp/requirements-mmpose.txt

RUN python -m pip install \
        "mmcv==2.1.0" \
        -f https://download.openmmlab.com/mmcv/dist/cu121/torch2.1/index.html

RUN python -m pip install --no-deps "mmdet==3.2.0" "mmpose==1.3.2"

RUN python - <<'PY'
from mmpose.apis import MMPoseInferencer
import cv2
import mmcv
import mmdet
import mmengine
import numpy
import torch

print("mmpose runtime import check ok")
print("torch", torch.__version__, "cuda", torch.version.cuda)
PY

COPY app ./app
COPY proto ./proto
COPY tools ./tools
COPY scripts ./scripts
COPY main.py pytest.ini ./

ENV PROCESSING_HTTP_HOST=0.0.0.0 \
    PROCESSING_HTTP_PORT=9000 \
    PROCESSING_GRPC_BIND=0.0.0.0:50051 \
    PROCESSING_PROCESSOR=mmpose_triangulation \
    PROCESSING_MMPOSE_DEVICE=cuda:0

EXPOSE 9000 50051

ENTRYPOINT ["python", "-m", "app.main"]
