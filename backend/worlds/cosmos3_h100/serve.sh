#!/usr/bin/env bash
# Usage: serve.sh GPU_INDEX PORT [MODEL_PATH_OR_HF_ID]
# Launches sglang serve for Cosmos3-Nano-Sim-Auto detached; echoes PID.
set -u
GPU=${1:?gpu index}; PORT=${2:?port}; MODEL=${3:-nvidia/Cosmos3-Nano-Sim-Auto}
ROOT=${COSMOS3_ROOT:-$PWD/cosmos3-spike}; ENV=${COSMOS3_ENV:-$ROOT/env}; LOGDIR=$ROOT/logs
export HF_HOME=${HF_HOME:-$ROOT/hf} TMPDIR=${TMPDIR:-$ROOT/tmp}
mkdir -p "$LOGDIR" "$TMPDIR/triton_cache" "$TMPDIR/inductor_cache"
export PATH=$ENV/bin:$PATH
export CUDA_VISIBLE_DEVICES=$GPU
# No nvcc on this box: force the Triton FlexAttention replay kernel (default on sm90 would be FA4/CuTe-DSL).
export SGLANG_DIFFUSION_COSMOS3_MULTIVIEW_ATTENTION_BACKEND=triton
export SGLANG_DIFFUSION_COSMOS3_LIDAR_USE_NATTEN=0      # LiDAR not used; never needs natten
export SGLANG_PERF_LOG_DIR=$LOGDIR/perf_gpu$GPU          # performance.log (per-stage JSON + memory)
export TRITON_CACHE_DIR=$TMPDIR/triton_cache TORCHINDUCTOR_CACHE_DIR=$TMPDIR/inductor_cache
export SGLANG_DIFFUSION_SYNC_STAGE_PROFILING=1           # accurate per-stage GPU timings (small overhead)
LOG=$LOGDIR/serve_gpu$GPU.log
# an old server on PORT would satisfy the readiness watcher; refuse before overwriting its pid/epoch files
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then echo "PORT_IN_USE $PORT" >&2; exit 1; fi
date +%s.%N > "$LOGDIR/serve_gpu${GPU}_start_epoch.txt"
rm -f "$LOGDIR/serve_gpu${GPU}_ready_epoch.txt"
nohup setsid sglang serve --model-path "$MODEL" --host 127.0.0.1 --port "$PORT" --num-gpus 1 \
  > "$LOG" 2>&1 < /dev/null &
PID=$!
echo $PID > "$LOGDIR/serve_gpu$GPU.pid"
# watcher: ready epoch = first time /health (or /v1/models) answers 200; load time = ready - start
nohup setsid bash -c '
  while kill -0 '"$PID"' 2>/dev/null; do
    for p in health v1/models; do
      if curl -sf -o /dev/null --noproxy "*" http://127.0.0.1:'"$PORT"'/$p; then
        date +%s.%N > '"$LOGDIR/serve_gpu${GPU}_ready_epoch.txt"'; exit 0; fi
    done; sleep 1
  done' > /dev/null 2>&1 < /dev/null &
echo "PID=$PID log=$LOG start_epoch_file=$LOGDIR/serve_gpu${GPU}_start_epoch.txt"
