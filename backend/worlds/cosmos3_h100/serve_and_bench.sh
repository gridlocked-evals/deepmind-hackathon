#!/bin/bash
# Usage: serve_and_bench.sh GPU PORT [FRAMES=61] -> start server, time load, run single-view bench cold + warm. Detach with nohup setsid.
set -u
GPU=$1; PORT=$2; FR=${3:-61}
HERE=$(cd "$(dirname "$0")" && pwd); W=${COSMOS3_ROOT:-$PWD/cosmos3-spike}; PY=${COSMOS3_ENV:-$W/env}/bin/python; L=$W/logs; R=$W/runs/gpu${GPU}_f${FR}_$(date +%Y%m%d_%H%M%S); mkdir -p $R
exec > $R/pipeline.log 2>&1
echo "RUN_DIR $R"; T0=$(date +%s.%N)
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv > $R/gpu_before.csv
bash $HERE/serve.sh $GPU $PORT
SPID=$(cat $L/serve_gpu$GPU.pid); echo "SERVER_PID $SPID"
# wait up to 60 min for ready
for i in $(seq 1 ${COSMOS3_READY_TIMEOUT_S:-3600}); do
  [ -f $L/serve_gpu${GPU}_ready_epoch.txt ] && break
  kill -0 $SPID 2>/dev/null || { echo SERVER_DIED; tail -60 $L/serve_gpu$GPU.log; exit 1; }
  sleep 1
done
[ -f $L/serve_gpu${GPU}_ready_epoch.txt ] || { echo SERVER_NOT_READY; tail -60 $L/serve_gpu$GPU.log; exit 1; }
S=$(cat $L/serve_gpu${GPU}_start_epoch.txt); Rd=$(cat $L/serve_gpu${GPU}_ready_epoch.txt)
echo "LOAD_S $(python3 -c "print(round($Rd-$S,1))")"
nvidia-smi --query-compute-apps=pid,used_memory --format=csv > $R/gpu_after_load.csv; cat $R/gpu_after_load.csv
export BENCH_GPU=$GPU; FAILED=0
for tag in cold warm; do
  ts=$(date +%s.%N)
  $PY $HERE/bench.py --port $PORT --frames $FR --gpu $GPU --out $R/$tag > $R/$tag.out 2>&1; rc=$?
  echo "BENCH_$tag rc=$rc wall=$(python3 -c "import time;print(round(time.time()-$ts,1))")"
  grep -E '"(gen_wall_s|throughput_fps|gpu_mem_peak_mib|error|status)"' $R/$tag/bench_result.json 2>/dev/null | head
  [ $rc -eq 0 ] || { echo "BENCH_$tag FAILED, skipping remaining runs"; FAILED=1; break; }
done
grep -nE 'attention backend|Sim-Auto rollout|Chunk [0-9]+/|finished in|stages created|Uvicorn|Error|Traceback' $L/serve_gpu$GPU.log | tail -200 > $R/serve_key_lines.txt
cp -r $L/perf_gpu$GPU $R/ 2>/dev/null
echo "TOTAL_PIPELINE_S $(python3 -c "import time;print(round(time.time()-$T0,1))")"
echo PIPELINE_DONE  # server left running for more workers / reuse
exit $FAILED  # callers must see a failed bench, after logs are collected
