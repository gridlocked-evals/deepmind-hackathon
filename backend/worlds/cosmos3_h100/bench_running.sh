#!/bin/bash
# Usage: bench_running.sh GPU PORT FRAMES TAG -> cold + warm single-view bench against an already-running server
GPU=$1; PORT=$2; FR=$3; TAG=$4; HERE=$(cd "$(dirname "$0")" && pwd); W=${COSMOS3_ROOT:-$PWD/cosmos3-spike}; PY=${COSMOS3_ENV:-$W/env}/bin/python; L=$W/logs
R=$W/runs/${TAG}_gpu${GPU}_f${FR}_$(date +%H%M%S); mkdir -p $R; exec > $R/pipeline.log 2>&1
LOGSTART=$(wc -l < $L/serve_gpu$GPU.log)
for tag in cold warm; do ts=$(date +%s.%N)
  $PY $HERE/bench.py --port $PORT --frames $FR --gpu $GPU --out $R/$tag > $R/$tag.out 2>&1
  echo "BENCH_$tag rc=$? wall=$(python3 -c "import time;print(round(time.time()-$ts,1))")"; done
tail -n +$LOGSTART $L/serve_gpu$GPU.log > $R/serve_log_slice.txt
grep -nE 'attention backend|Sim-Auto rollout|Chunk [0-9]+/|finished in|Error|Selected' $R/serve_log_slice.txt | cut -c1-300 > $R/serve_key_lines.txt
echo BENCH_ONLY_DONE
