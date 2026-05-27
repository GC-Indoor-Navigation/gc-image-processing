# syntax=docker/dockerfile:1

ARG BASE_IMAGE=pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

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
COPY sync_by_camera1_nearest_common_range ./sync_by_camera1_nearest_common_range

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
