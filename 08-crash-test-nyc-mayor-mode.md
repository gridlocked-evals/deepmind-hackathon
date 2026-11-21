# ⭐ Crash Test NYC: Mayor Mode + Robotaxi Test

**Status: very strong idea, Nov 21, 2026 (hackathon day).**

**One line:** pick any live NYC traffic camera, redesign the street as the mayor, walk it in a Genie 4 world, then watch a robotaxi agent try to drive it safely, with the Decisions API judging every second.

One product, two modes, same pipeline:

| | Mayor Mode (the fun part) | Robotaxi Test (the useful part) |
|---|---|---|
| **Who plays** | The judge or the audience | A Hydrogen driving agent |
| **What happens** | Pick a live corner, give an order ("busway on 7th Ave, plaza on the corner"), then walk or drive the new street | The agent drives the same redesigned street |
| **Scoring** | You drive against the agent; the Decisions API scores both | Decisions API judges each second ("in the bus lane?", "yielded to a pedestrian?"); fix the agent and re-run to 0 violations |
| **Why it matters** | The wow on stage | The proof: an eval on unseen cameras vs a single-call baseline |

**The useful question:** when the city changes a street, does a self-driving car still drive it safely? Today an AV company finds out after the change is built. This tests the street before it's built.

**Naming:** "Mamdani Mode" inside the team is fine. On stage, in the repo and in the submission, call it **Mayor Mode** and never generate his name or face. The Genie docs say real people and public figures can block `world_setup` or end a stream, and the rules ban legal/ethical/policy violations. Stick to visible street policies: bus lanes, busways, plazas, bike lanes, congestion pricing.

---

## How it fits today's rules

From the Gemini 4 NYC participant guide:
- **Track: Problem Statement One, "5 Sigma."** A real task agents struggle with (driving a changed street), an eval on inputs we didn't prepare (judge-picked live cameras), and a comparison against a simple baseline.
- **≥2 Gemini 4 models, each with a clear job**, plus the Decisions API (see below).
- **Beyond a text box:** camera frames in, a generated world out.
- **Not a banned project:** not a dashboard, not an image analyzer, not sports.
- **Judging:** live demo on unrehearsed inputs 35% · proof it works 25% · creativity 20% · Gemini 4 + Decisions API use 20%.
- **Prize angles:** main prizes, plus **Best Use of Managed Agents ($5K)** if the eval sweep runs on Managed Agents in parallel.
- **Repo must be public, and the demo may only show work built during the event.** See the warning at the end.

## Model jobs

| Model | Job |
|---|---|
| **Gemini 4 Argon** (`gemini-4-argon`) | Policy planner and scene writer: reads the live camera frame and the mayor's order, writes the Genie world prompt and the scenario (actors, hazards) |
| **Gemini 4 Hydrogen** (`gemini-4-hydrogen`) | Fast driving agent: looks at Genie frames and sends key presses |
| **Decisions API** (`gemini-decision-1`) | Live judge: yes/no and score questions on frames, with calibrated probabilities and no text output |
| **Genie 4** (`genie-4`) | The world: a walkable or drivable street seeded from the camera frame |

## Genie 4 facts that shape the build
From the AI Studio docs (`/docs/genie`, `/docs/models/genie-4`, `/docs/rate-limits`):
- **Session:** Interactions API `v1beta`, model `genie-4`. Python: `client.aio.interactions.create(...)` then `connect(...)`. Needs `google-genai` ≥ 2.3.0.
- **Seed:** one `world_setup` event with `world_prompt.text` plus an optional `reference_image` (base64 JPEG, **16:9 only**; 1280×720 or 640×360 suggested). Over WebRTC the message must stay under 64 KB, so the JPEG must be under about 48 KB.
- **Output:** 1280×720 at 24 fps. Over WebSocket each `step.delta` of type `video` is one JPEG, so individual frames can go straight to the Decisions API.
- **Controls:** `user_action` with `key_down` / `key_up` on `key_w/a/s/d` and arrow keys, about 0.2–0.5 s latency. Text steering via `input` events, about 1–2 s latency.
- **Limits:** **120 s hard cap per session**, coherence best in the first 45–90 s, no resume. Tier 1: 2 concurrent sessions, 50 per day.
- **Cost:** about $0.10/s, roughly $6/min. Always call `session.complete()`; just closing the socket keeps billing.
- **Known weaknesses:** objects out of view for about 8 s can reset; text and counts drift.

## 3-minute demo
1. **Pick a corner.** "Here's 7th Ave & 26th St, outside this building, 30 seconds ago." Show the live DOT camera.
2. **Mayor's order.** A judge types or says "Put a busway on 7th Ave and a plaza on the corner." Argon turns it into a Genie world from the live frame.
3. **Play it.** The judge walks the new street with WASD.
4. **Robotaxi run.** The Hydrogen agent drives it. Each second the Decisions API asks "In the bus lane? Yielded to the pedestrian in the plaza?"
5. **Verdict and fix.** "3 near-misses, all at the plaza edge." Give the agent the new street rules, re-run, 0 violations.

## Proof (the 25%)
Run 20 unseen cameras × 5 policies. For each run, measure:
- **Change fidelity:** did the requested bus lane or plaza actually appear? (Decisions API, yes/no)
- **Place fidelity:** does the world still look like the real corner? (Decisions API score vs the camera frame)
- **Driving safety:** violations per minute, our agent (Argon planner + Hydrogen driver + safety check before each action) vs a baseline of one model call per frame.
- **Where it breaks:** for example, Genie losing the redesign after about 40 s of walking. Show it honestly.
- **Judge calibration:** hand-label a sample of frames and check the Decisions API against them.

## Stretch modes (same pipeline, different agent and score)
- **Ambulance Mode:** does an ambulance get through faster or slower after the change?
- **Council Fight:** two players give opposite orders ("bike lane" vs "parking"); both render side by side and the agents score each.
- **Storm Stress Test:** steer Genie to "blizzard" or "flash flood"; does the redesign still work for the robotaxi? (the Element 119 angle)
- **Wheelchair Mode:** walk the corner as a wheelchair user before and after. A preview only, since Genie may invent curb ramps.

## Build order today (hacking 10:30, submissions 5:00 PM)
1. **10:30–11:15, go/no-go spike:** a DOT camera frame (352×240, cropped/upscaled to 16:9 JPEG) → Genie `world_setup` → frames come back → send W key → it moves. If camera frames make bad worlds, seed from a Street View-style 16:9 image of the same corner.
2. **Argon scene writer + Decisions API judge** on Genie frames.
3. **Hydrogen driving agent loop** and the baseline.
4. **By about 3 PM:** the eval sweep on unseen cameras (Managed Agents in parallel for the prize).
5. **Last:** UI polish, 1-minute submission video, recorded backup in case the Wi-Fi fails.

## How it relates to Gotham Forge
[GOTHAM_FORGE_PLAN.md](GOTHAM_FORGE_PLAN.md) builds a camera-grounded SUMO simulation. Crash Test NYC swaps SUMO for Genie 4 as the world and adds Mayor Mode. The camera directory and map selection can serve as the entry point to pick a corner.

> ⚠️ **Rule check:** the guide says the demo may only show work built during the hackathon, and presenting existing work as your own means disqualification. The Gotham Forge code was committed on Nov 19, before the event, and it adapts the earlier Pharos project. If any of it is used today, clearly label it as pre-existing in the demo and submission, or rebuild the needed parts today.
