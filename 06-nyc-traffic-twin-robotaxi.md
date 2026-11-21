# NYC Traffic Twin + Robotaxi Agents

**The idea:** use the intersection cameras to model NYC traffic in real time, then build simulation infrastructure where our agents act as autonomous vehicles (robotaxis) driving through Manhattan.

Data details are in [05-nyc-dot-cameras.md](05-nyc-dot-cameras.md). Benchmark research is in [07-evals-benchmarks-research.md](07-evals-benchmarks-research.md).

## Four iterations, stress-tested

### v1: every car is an LLM agent
**Fails.**
- An LLM takes about 1 second or more to respond; driving control needs about 10 decisions per second.
- 200 cars deciding every 5 seconds is about 40 model calls per second: expensive and likely to hit quota limits (one SF team's demo broke when their Gemini quota ran out).
- Dots on a map won't impress judges.

### v2: split the work into layers
**Fixes speed and cost.**
- **SUMO** (open-source traffic simulator) handles physics: following distance, lane changes, traffic lights.
- **Gemini Flash vehicle agents** make the bigger decisions: route, when to yield, where to pull over.
- **One Argon fleet dispatcher** places and rebalances robotaxis.
- **Every 60 seconds the cameras re-tune the simulation:** Gemini counts cars per frame, and the counts adjust demand in SUMO.

### v3: make the simulation prove it's real
**Fixes "why should I believe this?"**
- Show the real camera frame next to the simulation's view of the same intersection, with a match score that climbs as tuning runs ("Twin fidelity 0.52 → 0.86"). Same move as LabTwin's 54 → 88.

### v4: make the evals the product
**Fixes "so what?"**
- The fleet becomes one contestant on a benchmark. Hard moments from the cameras (a double-parked truck, a bus blocking a lane, a crowd jaywalking) become replayable test scenarios. Several agent strategies compete and get scored.

## Stress tests

| Stress test | Risk | Mitigation |
|---|---|---|
| Data fidelity | Snapshots only give counts, not paths | Tune to match queue length and car count per intersection; don't claim to track individual cars |
| Latency and cost | LLMs can't control a car at 10 decisions/sec | v2 layers; LLMs only make bigger decisions |
| "Is the sim real?" | Judges will ask | Show twin-fidelity score live, side by side with the camera |
| One-day scope | All of NYC is too big | Midtown only, about 1 km² and 78 cameras. Convert OpenStreetMap roads to SUMO with `netconvert` |
| Demo failure | Wi-Fi drops or API quota runs out | Pre-record a backup run; cache camera frames |
| Branding | "Waymo" is a trademark | Say "robotaxi fleet," not Waymo |

## Five directions

### A. Robotaxi Fleet Dispatcher
The core idea as of v2. A fleet of 200 robotaxis serving Midtown, demand tuned from live cameras.
- **Wow:** a judge closes 5th Ave on the map. The fleet re-routes within seconds; a live chart shows average wait time rising, then recovering.
- **Weakness:** optimization, not evals, unless paired with B.

### B. Gotham Gym: a driving benchmark built from live NYC ⭐
A scenario suite pulled from the cameras. Any driving agent plugs in and is scored on safety, efficiency and rule-following, with a leaderboard.
- **Wow:** "This scenario happened at Broadway & 42nd 7 minutes ago." The real frame plays, then four agents replay the moment, and one runs the red light.
- **Why it wins:** squarely evals and swarms, and it can't have leaked into training data because scenarios are minutes old.

### C. Signal Swarm: the agents are the traffic lights
One agent per intersection; neighboring lights negotiate timing.
- **Why it's credible:** the city controls lights, not cars. An unverified Nairobi study using SUMO reported LLM-timed lights cut waiting by up to 35% vs fixed timing.
- **Wow:** an ambulance enters Midtown and a green wave opens ahead of it across 12 intersections, with total delay dropping live.

### D. Red Team City
An adversarial swarm generates the hardest realistic scenarios it can, grounded in real camera frames, to break driving agents.
- **Wow:** a failure heatmap over Midtown. Patch the policy, re-run, and the red areas shrink.

### E. What If It Were All Robotaxis?
Freeze a real moment (Times Square, 5:42 PM), swap every human driver for an AV agent, and replay it.
- **Wow:** split screen of the real camera and the all-AV version, with the delay difference counting up live.

## Recommendation
**B with C or A as contestants.** Gotham Gym is the eval harness; the fleet or signal swarm is what gets tested. That gives a live wow, a real benchmark, and a clear answer to "is this real?"
