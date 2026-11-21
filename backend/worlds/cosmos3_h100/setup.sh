#!/usr/bin/env bash
# Build the SGLang env and fetch the Cosmos3-Nano-Sim-Auto checkpoint + NVIDIA daytime sample.
# Needs: Linux x86_64, NVIDIA driver with CUDA >= 13.0, gcc, python3.12. No nvcc, Rust, Docker or conda needed.
# Usage: COSMOS3_ROOT=/path/to/workdir ./setup.sh
set -euo pipefail
ROOT=${COSMOS3_ROOT:-$PWD/cosmos3-spike}
ENV=${COSMOS3_ENV:-$ROOT/env}
export HF_HOME=${HF_HOME:-$ROOT/hf} UV_CACHE_DIR=${UV_CACHE_DIR:-$ROOT/uv-cache} TMPDIR=${TMPDIR:-$ROOT/tmp}
mkdir -p "$ROOT/bin" "$TMPDIR" "$ROOT/logs"

# uv as a standalone binary (some hosts lack ensurepip, so `python3 -m venv` fails).
UV=$(command -v uv || true)
if [ -z "$UV" ]; then
  curl -sSfL https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz | tar xz -C "$TMPDIR"
  cp "$TMPDIR"/uv-x86_64-unknown-linux-gnu/uv* "$ROOT/bin/"; UV=$ROOT/bin/uv
fi

# The PyPI wheel ships the Sim-Auto pipeline with prebuilt Rust extensions; building
# sglang from git needs cargo. vllm-omni ships the Sim-Auto deploy config only in its Docker image.
"$UV" venv -q -p python3.12 "$ENV"
"$UV" pip install -p "$ENV/bin/python" --torch-backend cu130 "sglang[diffusion]==0.5.23" requests "huggingface_hub[hf_xet]"
"$ENV/bin/python" -c "import torch,sglang;print('torch',torch.__version__,torch.version.cuda,torch.cuda.is_available(),'sglang',sglang.__version__)"

# Both repos are ungated. Download each sample folder separately: one `hf download`
# call with several --include patterns silently dropped one of them.
HF="$ENV/bin/hf"
HF_XET_HIGH_PERFORMANCE=1 "$HF" download nvidia/Cosmos3-Nano-Sim-Auto --exclude "images/*"
for part in wsm rgb captions; do
  "$HF" download nvidia/Cosmos3-Nano-Transfer-Auto --include "assets/sample_data/daytime_scene/$part/*" --local-dir "$ROOT/sample"
done
ls "$ROOT/sample/assets/sample_data/daytime_scene/wsm/camera_front_wide_120fov.mp4" >/dev/null && echo SETUP_DONE
