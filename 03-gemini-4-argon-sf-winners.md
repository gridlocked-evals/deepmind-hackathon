# Gemini 4 Argon SF Hackathon: Oct 25, 2026

The latest SF edition and the closest template for Gemini 4 NYC. Hosted by Cerebral Valley and Google DeepMind. 128 projects, 6 placed.

- **Prizes ($20K cash):** 1st $7,500, 2nd $5,000, 3rd $2,500, Best Use of Managed Agents $5,000. The gallery shows no winner for the Managed Agents prize.
- **Final-round judges (Google):** Joana Carrasqueira, Shudhatma Jain, Subhasmita Sahoo, Oskar Yao
- **Tracks:** not listed on the event page. Some entries mention a "More Dimensions" track.
- **Argon / Managed Agents:** not defined on the pages. From usage, Argon appears to be a Gemini 4 model with a 1M-token context window, and Managed Agents appear to be cloud-hosted agents running in sandboxes. This is inferred, not stated.

## Winners

| Place | Project | What it does | The wow moment |
|---|---|---|---|
| 1st | **LabTwin** (team "polar bears": Dhilen Mistry, Jonathan Yang Tsang, Ben Xie) | Takes a lab protocol and a photo of the bench and builds a simulated twin of the wet lab. Runs the protocol step by step and traces each failure to the step that caused it. | The model rendered the twin from the photo's viewpoint and the visual match score went **54 → 88**. Then "Argon, fix it": it rewrote the protocol and re-ran the simulation to prove the fix. Managed Agents built the simulation (MuJoCo scene, RL environment) in parallel sandboxes. |
| 2nd | **Morf** (Kavish Poddar, Shiva Narayana Raju Birudaraju, Aradhya Mishra) | Describe an object and get an interactive 3D assembly, plus wiring, assembly steps and firmware. | Renders the assembly, checks it and corrects it, with measured before-and-after results. |
| 3rd | **eagle.** (Timeo Schmidt, solo) | Flight planning for small planes using FAA data, live webcams and satellite weather, then re-planning in flight. A voice agent calls the airport. | Mid-flight, the destination drops below safe weather. A cloud agent re-plans a diversion and texts the pilot, and the pilot replies "Y." |
| Finalist | **BEAMTEAM** (dave rauchwerk) | Pick a figure in an arXiv paper and get a verified laser-optics bench layout in under 3 minutes, with parts list and assembly PDF. Argon agents read the paper in parallel; a physics engine checks safety. | Parts fly onto a 3D bench and line up by themselves. |
| Finalist | **Revive** (Gautam Anand, Prathyek Poojary, Suhurrith Adhikari) | Rebuilds lost games from YouTube footage, ingesting all footage in one 1M-token call. | Managed Agents join a match over the internet to hunt for bugs nobody filmed. |
| Finalist | **Skywake** (Aniket Jatav) | A full 3D game made by 20+ managed agents acting as a game studio. | |

## Eval and swarm projects that didn't place
Worth knowing because our idea space overlaps:
- **Winner Signal:** checks whether a demo video's claims match the actual code and agent traces.
- **Carroll:** asks whether to trust an overnight agent's "16/16 tests pass" claim.
- **Task Factory:** turns real speedups into verified training tasks for coding agents.
- **Argon Speed:** five agents running a GPU kernel engineering team around the clock.
- **Argon CRS-RL:** a 4-agent cyber reasoning system plus an RL environment.
- **Blueshift:** red/green/blue agents for cyber defense.
- Also: Agent Gym (managed agents controlling humanoids), Playtest (game-testing agent), You Can Be a Founder (29 AI employees).

## Takeaway
Every placed project had three things:
1. A physical or visual domain.
2. A visible score that climbs on stage.
3. A "fix it" step that re-runs to prove the fix.

Pure eval projects with no visual payoff didn't place. Evals need to be something judges can watch, not a report.

## Sources
- [Event page](https://cerebralvalley.ai/e/gemini-4-argon-sf)
- [Gallery](https://cerebralvalley.ai/e/gemini-4-argon-sf/hackathon/gallery)
- [Winner Signal](https://cerebralvalley.ai/e/gemini-4-argon-sf/hackathon/gallery/93)
- [Skywake](https://cerebralvalley.ai/e/gemini-4-argon-sf/hackathon/gallery/65), [Revive](https://cerebralvalley.ai/e/gemini-4-argon-sf/hackathon/gallery/115)
- Placed project pages: gallery/67 (LabTwin), gallery/48 (Morf), gallery/75 (eagle.), gallery/7 (BEAMTEAM)
