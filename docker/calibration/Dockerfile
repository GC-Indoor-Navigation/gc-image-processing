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
    FORCE_CUDA=1 \
    LD_LIBRARY_PATH=/usr/local/cuda/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64

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

RUN python -m pip uninstall -y \
        opencv-python \
        opencv-python-headless \
        opencv-contrib-python \
        opencv-contrib-python-headless || true \
    && python -m pip install "opencv-contrib-python-headless==4.8.1.78"

RUN python -m pip install --no-build-isolation "chumpy==0.70"

RUN python - <<'PY'
from pathlib import Path
import site

site_packages = Path(site.getsitepackages()[0])
p = site_packages / "sitecustomize.py"

patch = """
# Compatibility patch for chumpy==0.70 with numpy>=1.24
try:
    import numpy as _np
    _np.bool = bool
    _np.int = int
    _np.float = float
    _np.complex = complex
    _np.object = object
    _np.str = str
    _np.unicode = str
except Exception:
    pass
"""

old = p.read_text(encoding="utf-8") if p.exists() else ""
if "Compatibility patch for chumpy==0.70" not in old:
    p.write_text(old + "\n" + patch + "\n", encoding="utf-8")

print("patched:", p)
PY

RUN python - <<'PY'
from pathlib import Path
import site

targets = []
for sp in site.getsitepackages():
    p = Path(sp) / "chumpy" / "__init__.py"
    if p.exists():
        targets.append(p)

if not targets:
    raise SystemExit("chumpy __init__.py not found")

p = targets[0]
txt = p.read_text(encoding="utf-8")

old = "from numpy import bool, int, float, complex, object, unicode, str, nan, inf"
new = """import numpy as _np
import builtins as _builtins
bool = _builtins.bool
int = _builtins.int
float = _builtins.float
complex = _builtins.complex
object = _builtins.object
unicode = _builtins.str
str = _builtins.str
nan = _np.nan
inf = _np.inf"""

if old in txt:
    txt = txt.replace(old, new, 1)
    p.write_text(txt, encoding="utf-8")
    print("patched chumpy:", p)
elif "import builtins as _builtins" in txt:
    print("chumpy already patched:", p)
else:
    print("target:", p)
    raise SystemExit("expected chumpy numpy import line not found")
PY

# CasCalib README asks for pytorch3d from facebookresearch/pytorch3d@stable.
# Use a pinned ref by default for repeatable Docker builds; override this build
# arg only when the torch/CUDA combination has changed.
ARG PYTORCH3D_REF=75ebeeaea0908c5527e7b1e305fbc7681382db47
RUN python -m pip install "iopath==0.1.10" "fvcore==0.1.5.post20221221" \
    && python -m pip install --no-build-isolation "git+https://github.com/facebookresearch/pytorch3d.git@${PYTORCH3D_REF}"

COPY tools ./tools

RUN python - <<'PY'
import sys
from pathlib import Path

import cv2
import chumpy
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
print("chumpy ok")
print("cv2", cv2.__version__)
print("numpy", numpy.__version__)
PY

ENTRYPOINT ["python", "/workspace/gc-image-processing/tools/calibration/run_pipeline.py"]
