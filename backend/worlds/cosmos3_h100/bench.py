#!/usr/bin/env python3
"""Single-view Cosmos3-Nano-Sim-Auto bench against a local sglang server (stdlib + requests)."""
import argparse, json, os, shutil, subprocess, sys, threading, time
import requests

SAMPLE = os.environ.get("COSMOS3_SAMPLE") or os.path.join(
    os.environ.get("COSMOS3_ROOT", os.path.join(os.getcwd(), "cosmos3-spike")),
    "sample/assets/sample_data/daytime_scene")

def sh(cmd):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e: return f"ERR {e}"

def env_info():
    d = {}
    for mod in ("torch", "sglang", "triton", "av", "imageio"):
        try: d[mod] = getattr(__import__(mod), "__version__", "?")
        except Exception as e: d[mod] = f"unavailable: {type(e).__name__}"
    try:
        import sglang, pathlib
        d["sglang_git_commit"] = sh(["git", "-C", str(pathlib.Path(sglang.__file__).resolve().parent), "rev-parse", "HEAD"])
    except Exception: pass
    d["nvidia_smi"] = sh(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    return d

def gpu_index_for_port(port):
    # server was launched with CUDA_VISIBLE_DEVICES=<gpu>; map via serve_gpu*.log naming is not possible, so use env/arg
    return os.environ.get("BENCH_GPU", "0")

def sampler(gpu, stop, samples):
    while not stop.is_set():
        out = sh(["nvidia-smi", "-i", str(gpu), "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
        try: samples.append((time.time(), int(out.split()[0])))
        except Exception: pass
        stop.wait(0.5)

def probe(path):
    if shutil.which("ffprobe"):
        out = sh(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                  "stream=width,height,nb_read_frames,r_frame_rate", "-of", "json", path])
        try:
            s = json.loads(out)["streams"][0]
            return {"frames": int(s["nb_read_frames"]), "width": s["width"], "height": s["height"], "fps": s["r_frame_rate"], "tool": "ffprobe"}
        except Exception: pass
    try:
        import av
        with av.open(path) as c:
            st = c.streams.video[0]
            n = sum(1 for _ in c.decode(st))
            return {"frames": n, "width": st.codec_context.width, "height": st.codec_context.height, "fps": str(st.average_rate), "tool": "av"}
    except Exception: pass
    try:
        import imageio.v3 as iio
        arr = iio.imread(path)
        return {"frames": int(arr.shape[0]), "height": int(arr.shape[1]), "width": int(arr.shape[2]), "tool": "imageio"}
    except Exception as e:
        return {"error": str(e)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--frames", type=int, default=61)
    ap.add_argument("--size", default="832x480")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--camera", default="camera_front_wide_120fov")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gpu", default=os.environ.get("BENCH_GPU", "0"), help="physical GPU index to sample")
    ap.add_argument("--timeout", type=float, default=3600)
    a = ap.parse_args()
    if (a.frames - 1) % 4: sys.exit("--frames must be 4k+1")
    os.makedirs(a.out, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"
    s = requests.Session(); s.trust_env = False
    cap = json.load(open(f"{SAMPLE}/captions/{a.camera}.json"))["caption_structured"]["chunk_0_300"]["caption"]
    extra = {"wsm": {},
             "multiview": {"views": [{"camera_key": a.camera, "prompt": cap,
                                      "control_path": f"{SAMPLE}/wsm/{a.camera}.mp4",
                                      "vision_path": f"{SAMPLE}/rgb/{a.camera}.mp4"}],
                           "condition_video_as_image": True, "num_frames": a.frames},
             "resolution": "480", "aspect_ratio": "16,9"}
    form = {"model": "nvidia/Cosmos3-Nano-Sim-Auto", "prompt": cap, "size": a.size, "num_frames": str(a.frames),
            "fps": "30", "guidance_scale": "1", "num_inference_steps": "4", "seed": str(a.seed),
            "extra_params": json.dumps(extra), "perf_dump_path": os.path.abspath(f"{a.out}/perf_dump.json")}
    samples, stop = [], threading.Event()
    th = threading.Thread(target=sampler, args=(a.gpu, stop, samples), daemon=True); th.start()
    result = {"request_form": form, "env": env_info(), "port": a.port, "gpu_sampled": a.gpu}
    t0 = time.time()
    try:
        r = s.post(f"{base}/v1/videos", files=[(k, (None, v)) for k, v in form.items()], timeout=120)
        result["submit_http"] = r.status_code; result["submit_body"] = r.text[:500]
        r.raise_for_status()
        job = r.json(); vid = job["id"]; result["video_id"] = vid
        polls = []
        while True:
            job = s.get(f"{base}/v1/videos/{vid}", timeout=30).json()
            polls.append((round(time.time() - t0, 2), job.get("status"), job.get("progress")))
            if job.get("status") in ("completed", "failed"): break
            if time.time() - t0 > a.timeout: job["status"] = "timeout"; break
            time.sleep(1)
        t1 = time.time()
        result.update(final_job=job, polls=polls[::max(1, len(polls) // 60)] + polls[-1:], gen_wall_s=round(t1 - t0, 2))
        if job.get("status") == "completed":
            mp4 = os.path.join(a.out, f"{vid}.mp4")
            with s.get(f"{base}/v1/videos/{vid}/content", stream=True, timeout=300) as c:
                c.raise_for_status()
                with open(mp4, "wb") as f:
                    for ch in c.iter_content(1 << 20): f.write(ch)
            result["download_s"] = round(time.time() - t1, 2)
            result["video"] = probe(mp4); result["mp4"] = mp4
            video = result["video"]
            if "error" in video or any(type(video.get(k)) is not int or video[k] <= 0
                                       for k in ("frames", "width", "height")):
                raise RuntimeError(f"Downloaded video failed validation: {video}")
            result["throughput_fps"] = round(video["frames"] / result["gen_wall_s"], 3)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        stop.set(); th.join(2)
    if samples:
        result["gpu_mem_peak_mib"] = max(m for _, m in samples)
        result["gpu_mem_baseline_mib"] = samples[0][1]
        result["gpu_mem_samples"] = len(samples)
    json.dump(result, open(os.path.join(a.out, "bench_result.json"), "w"), indent=2, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "polls"}, indent=2, default=str))
    # nonzero unless the video was generated, downloaded and decoded, so wrappers stop on unusable output
    return 0 if "mp4" in result and "error" not in result else 1

if __name__ == "__main__":
    sys.exit(main())
