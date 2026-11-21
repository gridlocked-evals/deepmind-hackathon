# Gridlocked

Explore New York through its public roadway cameras, pick a street, and give Gemini the wheel of a simulated car. **Cameras** browses the full public NYC DOT directory across all five boroughs and configured 511NY video feeds. **Route** selects a starting point and destination for a street-following journey. **Driver** lets Gemini choose the ego vehicle's target speed while deterministic steering and physics follow that route.

**Traffic demo** restores the original Vestry Street scene: 24 surrounding vehicles and the ego SUV, authored signals, and a 60-second replay. **Start scenario with Gemini** uses the backend's existing Gemini credential for up to 60 Decisions model decisions and 60 simulated seconds. The car keeps moving while the next decision is requested, with a one-second refresh interval and a three-second command freshness limit. Camera footage runs independently; model driving uses structured simulated state. Background traffic remains authored replay, and camera-image perception is not connected to this view.

**NYC journey** runs successive street trips with Carbon choosing hazards. It continues automatically and restarts from a random NYC point after 25 simulated seconds without meaningful progress. See [continuous journeys](docs/CONTINUOUS_JOURNEY.md) for controls, retained results, and timing limits.

**[Setup and controls](RUNNING.md)** · [Driver API](docs/DRIVER_API.md) · [Live driver verification](docs/DRIVER_VERIFICATION.md) · [Architecture](docs/ARCHITECTURE.md) · [Camera API](docs/CAMERA_API.md) · [Verification](docs/VERIFICATION.md)

[Implementation plan](GOTHAM_FORGE_PLAN.md) · [Element 119 contracts](docs/CONTRACTS.md) · [Source attribution](THIRD_PARTY_NOTICES.md)

Quick start after installing dependencies: run `npm run dev:api` and `npm run dev` in separate terminals. Cameras, route previews, and traffic replay need no model credential. Gemini driving uses optional backend-only `GEMINI_API_KEY` or `GOOGLE_API_KEY` configuration; no key goes into the browser.

The repository also implements capture storage, Carbon edits, Nano Banana references, Decisions gates, a bounded Genie runtime, and evaluation components. See [edit/reference/gate integration](docs/EDIT_REFERENCE_GATE.md) and [Genie runtime](docs/GENIE_RUNTIME.md) for their contracts and remaining integration work. The simulator driver is separate from the visual Genie navigation controller.

## Hackathon provenance (Nov 21, 2026)

Paradox560's latest clarification on Nov 21, 2026 is that the starting React/Vite app shell, Vestry traffic scene, 3D vehicle rendering, and camera/HLS proxy were **written during the hackathon**. The maintainer explicitly reconfirmed this after being shown the earlier "A mix / uncertain" wording; this clarification supersedes that earlier wording. This records the maintainer's statement of build timing. The geographic map and camera playback architecture also adapt Pharos, as recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Changes merged on the event day**

The PRs below record integration on November 21. Merge dates establish when changes entered this repository, not when every part was originally written. This inventory is not a determination of demo eligibility.

| Piece | Code | PRs |
|---|---|---|
| Model gateway, usage ledger, budgets, capability checks | `backend/gateway/` | #25, #39, #40 |
| Genie session runtime, streaming, frame recording, explicit stop | `backend/worlds/` | #26, #40, #53 |
| Bounded camera capture with immutable episode manifests | `backend/capture.py`, `backend/capture_api.py` | #35, #36, #49 |
| Hydrogen street-evidence extraction | `backend/observation*.py` | #58 |
| Carbon street edits, Nano Banana references, Decisions gate, gate → Genie binding | `backend/edits.py`, `backend/references.py`, `backend/gates.py` | #35, #52, #55 |
| Evaluation core: temporal judge, locked eval set, breakpoints, Carbon repair, adversary scoring | `backend/evaluation/` | #27, #43, #44, #54 |
| Citywide camera directory and street-route playback | `src/` | #34 |
| Shared Element 119 contracts | `docs/CONTRACTS.md`, `contracts/` | #35 |

**Third-party models and data:** Gemini 4, Decisions API, Nano Banana and Genie models (Google); NYC DOT and 511NY camera feeds; OpenStreetMap map and routing data. Attribution and adaptation details are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

# DeepMind Hackathon: Gemini 4 NYC Hackathon Research

Research and idea notes for the **Gemini 4 NYC Hackathon** (Cerebral Valley × Google DeepMind, Sat Nov 21, 2026).

## Files

| File | What's in it |
|---|---|
| [01-gemini-4-nyc-event.md](01-gemini-4-nyc-event.md) | The event we're entering: date, prizes, themes, format |
| [02-past-gemini-hackathon-winners.md](02-past-gemini-hackathon-winners.md) | Winners of earlier Gemini / DeepMind hackathons (NYC, Seoul, SF, Kaggle) |
| [03-gemini-4-argon-sf-winners.md](03-gemini-4-argon-sf-winners.md) | The latest SF edition (Oct 2026): winners, wow moments, what placed vs. didn't |
| [04-agent-evals-swarm-ideas.md](04-agent-evals-swarm-ideas.md) | First round of agent evals / swarm ideas, each with a wow moment |
| [05-nyc-dot-cameras.md](05-nyc-dot-cameras.md) | NYC DOT traffic camera data (verified) and camera + agent ideas |
| [06-nyc-traffic-twin-robotaxi.md](06-nyc-traffic-twin-robotaxi.md) | The robotaxi / traffic digital twin idea: iterations, stress tests, 5 directions |
| [07-evals-benchmarks-research.md](07-evals-benchmarks-research.md) | Existing driving and multi-agent benchmarks, what to borrow, proposed scorecard |
| [08-crash-test-nyc-mayor-mode.md](08-crash-test-nyc-mayor-mode.md) | ⭐ **Very strong idea:** Crash Test NYC, Mayor Mode (fun) + Robotaxi Test (useful) on Genie 4 |
| [09-FINAL-IDEA.md](09-FINAL-IDEA.md) | 🏆 **FINAL IDEA:** Crash Test NYC, Remastered: Nano Banana restyles the camera frame (any graphics style) before Genie 4 |

## Where we landed

- **What wins at these events:** a physical or visual domain, a score that visibly climbs on stage, and a "fix it" step that re-runs to prove the fix. Several pure-eval projects in SF didn't place, so evals need to be something judges can watch.
- **🏆 FINAL IDEA (Nov 21): Crash Test NYC, Remastered.** Nano Banana remasters the live camera frame into a clean street-level scene in any graphics style (photoreal to video-game), with the mayor's redesign drawn in. Genie 4 turns it into a playable world, and a robotaxi agent drives it while the Decisions API judges. See [09-FINAL-IDEA.md](09-FINAL-IDEA.md).
- **⭐ Very strong idea (Nov 21):** **Crash Test NYC**. Pick a live NYC camera, redesign the street as the mayor (Mayor Mode), walk it in a Genie 4 world, then a robotaxi agent drives it while the Decisions API judges safety (Robotaxi Test). See [08-crash-test-nyc-mayor-mode.md](08-crash-test-nyc-mayor-mode.md).
- **Earlier lead:** **Gotham Gym**, a live driving benchmark built from NYC DOT intersection cameras. Scenarios are pulled from real camera moments in Midtown and replayed in a SUMO digital twin. Agent strategies (a robotaxi fleet dispatcher, or a traffic-signal swarm) compete on a safety / efficiency / coordination scorecard.
- **Runner-up:** **Signal Swarm**, where the agents are the traffic lights rather than the cars.
