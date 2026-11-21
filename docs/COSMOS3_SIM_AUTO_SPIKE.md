# Cosmos3-Nano-Sim-Auto H100 spike — November 21, 2026

Issue #23, step 1 only. One bounded smoke test of
[nvidia/Cosmos3-Nano-Sim-Auto](https://huggingface.co/nvidia/Cosmos3-Nano-Sim-Auto)
on one lab H100 PCIe 80 GB, using NVIDIA's own daytime sample inputs. It was not
conditioned on our simulator state, and it is not a perception-policy or closed-loop
evaluation. Steps 2–3 of #23, which use exported map and actor controls, are blocked
on #19 and #20.

## Checkpoint facts

| | |
| --- | --- |
| License | OpenMDW 1.1 (commercial and non-commercial use) |
| HF access | Not gated; no token needed (checked 2026-11-21) |
| Size | 16B parameters; transformer 30.3 GB bf16 (6 shards), VAE 1.4 GB, LiDAR VAE included |
| Snapshot | `bea7452062866fdd8eebe4c38f305d02baed3699` |
| Camera rig | Hyperion 8.1, 1–11 views. A custom rig needs Transfer-Auto post-training followed by re-distillation, so a roadside or NYC DOT camera is **not** a supported view |
| Sampler | Fixed by distillation: 4 steps, guidance 1, causal chunks of 3 latent frames (12 pixel frames) |
| Resolutions | 480p (832×480) or 720p |

## Runtime findings

- **Backend.** vLLM-Omni ships the Sim-Auto deploy config (`cosmos3_nano_sim_auto.yaml`)
  only inside the `vllm/vllm-omni:cosmos3` Docker image; no PyPI wheel up to 0.34.0rc1
  includes it. Our host account has no Docker access. SGLang supports it on PyPI:
  `sglang[diffusion]==0.5.23` with torch 2.14.1+cu130. Building SGLang from git
  instead needs a Rust toolchain.
- **No nvcc needed.** By default the multiview attention picks FA4, a CuTe-DSL JIT,
  on Hopper. We force the Triton FlexAttention path with
  `SGLANG_DIFFUSION_COSMOS3_MULTIVIEW_ATTENTION_BACKEND=triton`. That path compiles
  with Inductor and Triton, so it needs gcc but no CUDA toolkit.
- **Fits on one H100 80 GB** with about 40 GB to spare (numbers below).
- **Request format.** Send the form as multipart (`files=[(k, (None, v))]`). A
  urlencoded body silently drops `extra_params.multiview`, and the server then
  rejects the request with "need multiview.views".

## Measurements (single view, low resolution, no LiDAR)

Config: GPU 1 of 2 (H100 PCIe, PCIe Gen2 host link, driver 580 / CUDA 13.0),
camera `camera_front_wide_120fov` only, 832×480, 61 frames at 30 fps, I2V (first
RGB frame plus WSM control), seed 0, 4 steps, guidance 1, Triton attention, no
LiDAR request.

| Measurement | Observed result |
| --- | --- |
| Server load (process start → `/health` 200) | **98.3 s**. Weights read in about 6 s from page cache; the rest is init plus the built-in 11-view warmup |
| GPU memory after load / warmup | 39.5 GB (worker process) |
| Peak GPU memory during request | **36.3 GB** (nvidia-smi, sampled every 0.5 s) |
| Output | 61 frames, 832×480 (ffprobe) |
| Request 1 (cold): submit → completed | 12.07 s; 17.0 s including client download |
| Request 2 (warm): submit → completed | about 11 s; 11.7 s including download |
| End-to-end throughput | **5.05 fps** cold, about 5.3 fps warm (output frames ÷ submit→completed) |
| Rollout stage only (6 chunks) | 7.16 s cold (includes the Triton compile); **3.33 s warm ≈ 18 fps** |
| Per-chunk (warm) | about 0.55 s per 12-frame chunk; the first chunk is the conditioning frame |
| Other warm stages | input 0.72 s, latent prep 0.37 s, VAE decode 1.52 s |
| Total wall (start server → both requests done) | about 2 min |

**First-frame latency.** The OpenAI Videos API returns the clip only when it is
complete, so a client sees its first frame after about 11 s for 61 frames. Inside
the server, chunk 1 was logged in the same second the rollout started, and the log
has one-second resolution. A precise first-chunk number needs the streaming or
realtime path, or sub-second stage timestamps from `SGLANG_PERF_LOG_DIR`.

The warm stages add up to about 6 s, while the request took about 11 s. The rest is
MP4 encoding, queueing, and the bench's 1 s poll interval, none of which we
profiled. NVIDIA reports 10.2 fps on an H100 for 1 view at 480p, using vLLM-Omni
with 201 frames. That figure is not directly comparable to our 61-frame SGLang run;
a 201-frame run is the next measurement.

## Not done yet

- More workers. The plan is one independent server per GPU, since the two GPUs
  have no NVLink and sit on separate NUMA nodes. It didn't run because host SSH
  access dropped after the first successful run.
- A 201-frame run, multi-view runs, LiDAR.
- Everything conditioned on our simulator state (#19, #20).

## Acceptance rules carried from #23

- Generated views that change geometry or events are excluded from scored
  perception-policy tests.
- Renders of a finished rollout are labeled **visualization**, not closed-loop
  evaluation.
- Before any comparison, we check that the generated actors match the commanded
  state: identity, count, projection, signal state and emergence timing.
- Nothing on the H100s may block the Genie/SUMO path or the submission.
- Any image-policy results are reported separately.

## Reproduce

Scripts are in `backend/worlds/cosmos3_h100/`. Paths come from `COSMOS3_ROOT`,
`COSMOS3_ENV` and `HF_HOME`.

```bash
export COSMOS3_ROOT=/path/with/40GB/free
backend/worlds/cosmos3_h100/setup.sh                       # env + checkpoint + sample
nohup setsid backend/worlds/cosmos3_h100/serve_and_bench.sh 1 30000 61 &   # GPU 1, port 30000
# results: $COSMOS3_ROOT/runs/<run>/{pipeline.log,cold,warm,serve_key_lines.txt}
# more runs against the same server:
backend/worlds/cosmos3_h100/bench_running.sh 1 30000 201 f201
```

The server keeps running after the bench finishes; stop it with
`kill $(cat $COSMOS3_ROOT/logs/serve_gpu1.pid)`.

## Regression checks

Run `python -m pytest backend/test_cosmos3_bench.py -q` on Linux to cover
generation failures, unreadable or empty downloads, wrapper exit codes,
readiness timeouts, and occupied-port protection without a GPU. The Python
client tests also run on Windows. Four shell cases are explicitly skipped
there: they require POSIX paths, executable permissions, and process signals;
the Windows WSL launcher is not a substitute for running pytest inside Linux.

These tests use local fixtures and do not repeat the H100 performance measurements.
