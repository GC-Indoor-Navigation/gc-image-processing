# syntax=docker/dockerfile:1

ARG BASE_IMAGE=pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime
ARG MMPOSE_BASE_IMAGE=gc-mmpose-processing:smoke

FROM ${BASE_IMAGE} AS smoke

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY tools ./tools

ENTRYPOINT ["python", "/workspace/tools/calibration/run_pipeline.py"]


FROM smoke AS calibration

COPY docker/calibration/requirements-calibration.txt /tmp/requirements-calibration.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements-calibration.txt

# MMPose and PyTorch3D are version-sensitive. Keep them opt-in until the target
# CUDA/PyTorch combination is fixed for the calibration host.
ARG INSTALL_MMPOSE=false
RUN if [ "${INSTALL_MMPOSE}" = "true" ]; then \
        python -m pip install -U openmim && \
        mim install "mmengine" "mmcv>=2.0.0" "mmdet>=3.0.0" && \
        python -m pip install "mmpose>=1.3.0"; \
    fi

ARG INSTALL_PYTORCH3D=false
ARG PYTORCH3D_PACKAGE=pytorch3d
RUN if [ "${INSTALL_PYTORCH3D}" = "true" ]; then \
        python -m pip install "${PYTORCH3D_PACKAGE}"; \
    fi


FROM ${MMPOSE_BASE_IMAGE} AS full

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    CUDA_HOME=/usr/local/cuda \
    FORCE_CUDA=1

ARG MAX_JOBS=1
ARG TORCH_CUDA_ARCH_LIST=8.6
ENV MAX_JOBS=${MAX_JOBS} \
    TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}

WORKDIR /workspace/gc-image-processing

# Keep the already verified MMPose image stack:
# - torch==2.1.0 + cu121
# - torchvision==0.16.0
# - mmcv==2.1.0
# - mmdet==3.2.0
# - mmpose==1.3.2
#
# CasCalib's last_capstone requirements are intentionally looser. Install the
# same package family with pinned versions from docker/calibration so the image
# stays reproducible while preserving the working MMPose stack.
COPY docker/calibration/requirements-calibration.txt /tmp/requirements-calibration.txt
RUN python -m pip install -r /tmp/requirements-calibration.txt

# CasCalib README asks for pytorch3d from facebookresearch/pytorch3d@stable.
# Use a pinned ref by default for repeatable Docker builds; override this build
# arg only when the torch/CUDA combination has changed.
ARG PYTORCH3D_REF=75ebeeaea0908c5527e7b1e305fbc7681382db47
RUN python -m pip install "git+https://github.com/facebookresearch/pytorch3d.git@${PYTORCH3D_REF}"

COPY tools ./tools

RUN python - <<'PY'
import sys
from pathlib import Path

import cv2
import mmpose
import numpy
import pytorch3d
import torch
from mmpose.apis import MMPoseInferencer

cascalib_path = Path("/workspace/gc-image-processing/tools/calibration/vendor/CasCalib")
sys.path.insert(0, str(cascalib_path))
import bundle_adjustment  # noqa: F401
import data  # noqa: F401
import geometry  # noqa: F401
import ICP  # noqa: F401
import time_align  # noqa: F401

print("calibration full import check ok")
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("mmpose", mmpose.__version__)
print("pytorch3d", getattr(pytorch3d, "__version__", "unknown"))
print("cv2", cv2.__version__)
print("numpy", numpy.__version__)
PY

ENTRYPOINT ["python", "/workspace/gc-image-processing/tools/calibration/run_pipeline.py"]
