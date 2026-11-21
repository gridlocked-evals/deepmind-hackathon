# Gotham Forge simulation workspace

Interactive NYC city map, a detailed illustrative Vestry Street traffic simulation, and toggleable public camera feeds.

**[Setup and controls](RUNNING.md)** · [Architecture](docs/ARCHITECTURE.md) · [Camera API](docs/CAMERA_API.md) · [Verification](docs/VERIFICATION.md)

[Implementation plan](GOTHAM_FORGE_PLAN.md) · [Source attribution](THIRD_PARTY_NOTICES.md)

Quick start after installing dependencies: run `npm run dev:api` and `npm run dev` in separate terminals. No model credentials are needed.

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

## Where we landed

- **What wins at these events:** a physical or visual domain, a score that visibly climbs on stage, and a "fix it" step that re-runs to prove the fix. Several pure-eval projects in SF didn't place, so evals need to be something judges can watch.
- **Current lead:** **Gotham Gym**, a live driving benchmark built from NYC DOT intersection cameras. Scenarios are pulled from real camera moments in Midtown and replayed in a SUMO digital twin. Agent strategies (a robotaxi fleet dispatcher, or a traffic-signal swarm) compete on a safety / efficiency / coordination scorecard.
- **Runner-up:** **Signal Swarm**, where the agents are the traffic lights rather than the cars.
