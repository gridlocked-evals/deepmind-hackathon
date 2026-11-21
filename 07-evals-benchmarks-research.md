# Evals and Benchmarks for Agents

What existing driving, traffic and multi-agent benchmarks teach, and how to use each in our project.

| Benchmark | Lesson | How we'd use it |
|---|---|---|
| **Waymo Open Sim Agents Challenge (WOSAC)** | Judges simulated traffic by how well it matches real recorded traffic, combining 9 measures (motion, interactions, map) into one realism score. 2025 leaderboard top ~0.79; replaying the real logs scores 0.83 | Our twin-fidelity score: does the simulation's car count and queue length match the cameras? |
| **Bench2Drive** | Testing on fixed recorded data doesn't reflect real driving; agents must be tested live in the simulation. Score combines route completion with penalties for crashes and violations. 220 short scenario routes | Same scoring formula for each agent |
| **Bench2Drive-Robust** (2026) | Tests agents with failed camera feeds, wrong position data and delayed control | Stress tests: drop camera feeds, add latency |
| **Bench2Drive-VL** (Apr 2026) | Asks vision-language driving agents questions mid-run in CARLA; lets VLMs plug in directly | Have agents explain decisions mid-run and score the explanation |
| **Silo-Bench** (Mar 2026) | 30 tasks, 1,620 experiments: adding more agents doesn't get around context limits | Plot agent count vs score. "More agents isn't better" is a good on-stage finding |
| **Open-ended coordination benchmark** (Jun 2026) | Communication matters most for coordination; memory and reasoning help multi-step plans | Run the swarm with and without inter-agent communication |
| **SwarmBench** | Tests LLM agents coordinating with only local perception, no central control | Fits Signal Swarm: each light sees only its own camera |
| **τ-bench** | Reports pass^k: does the agent succeed on every one of k repeated runs? | Run each scenario several times; report reliability, not one lucky run |
| **Camera counts → SUMO with an LLM** (arXiv 2505.21349) | 36 intersections: vision-based car counts from camera footage, then an LLM folds stakeholder feedback into route calibration | Closest prior work to our calibration pipeline |
| **Nairobi LLM signal study** | LLM agents set green times in a SUMO model calibrated from counts; reported up to 35% less waiting, 12–18% more throughput (single study, unverified) | Baseline claim to beat or check for Signal Swarm |

No standard benchmark was found that combines camera-calibrated SUMO networks with LLM signal control. That gap is our opening.

## Proposed scorecard (per agent)
- **Safety:** collisions and near-misses
- **Rule-following:** red lights, blocked crosswalks
- **Efficiency:** trip time, wasted mileage
- **Coordination:** deadlocks, gridlock
- **Robustness:** score drop under stress tests (dropped feeds, latency)
- **Cost:** dollars per hour of simulated driving
- **Twin fidelity:** how closely the simulation matches the cameras

## Sources
- [WOSAC paper](https://arxiv.org/pdf/2305.12032), [Waymo Sim Agents challenge](https://waymo.com/open/challenges/2025/sim-agents), [WOSAC overview](https://www.emergentmind.com/topics/waymo-open-sim-agents-challenge-wosac)
- [Bench2Drive-VL paper](https://arxiv.org/abs/2604.01259), [Bench2Drive-VL repo](https://github.com/Thinklab-SJTU/Bench2Drive-VL), [Bench2Drive-Robust repo](https://github.com/Thinklab-SJTU/Bench2Drive-Robust), [Bench2Drive paper](https://arxiv.org/pdf/2406.03877.pdf)
- [Silo-Bench](https://arxiv.org/abs/2603.01045), [open-ended coordination benchmark](https://arxiv.org/abs/2606.08340), [SwarmBench](https://arxiv.org/html/2505.04364v4)
- [Camera counts to SUMO with an LLM](https://arxiv.org/html/2505.21349v2), [Nairobi LLM signal study](https://ir.kabarak.ac.ke/bitstreams/1a45abc8-adcb-4c87-86f6-610b305dd40b/download), [SUMO vs data-driven simulators](https://arxiv.org/html/2512.18537v1)
