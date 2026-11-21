# Agent Evals + Agent Swarm Ideas (round 1)

Each idea has a wow moment you can show on stage.

1. **Swarm QA: "Hand us your URL."** A judge gives you any website live. 30 persona agents (a screen-reader user, a non-English speaker, an impatient shopper, someone trying to break in) run in parallel sandboxes, shown as a grid of 30 live browsers.
   - **Wow:** about 90 seconds later the grid turns red where agents got stuck. Click a tile to see a video of the exact moment it failed. Then "fix it": an agent opens a fix, the same 30 agents re-run, and the tiles turn green.
2. **Org Chart Race: which swarm design actually works?** One task goes to five teams with different structures: solo agent, assembly line, debate, manager with workers, and a market where agents bid for subtasks.
   - **Wow:** a live race on quality, cost and time. The surprise ending is that the solo agent beats the 20-agent team on some tasks. Then the system recommends the best team design for your task.
3. **Liar Detector for agents.** A worker agent does a long task overnight and claims "all tests pass." A swarm of auditors replays its run in sandboxes.
   - **Wow:** the auditors catch it cheating (deleting a test, hardcoding the answer) with a timestamped replay. Plant a known cheat for the demo.
   - **Caveat:** Carroll tried this angle in SF and didn't place.
4. **Any video becomes a benchmark.** Paste a YouTube video of someone doing a task. Argon turns it into a simulated environment with a grader, and 20 agents compete.
   - **Wow:** "This video became a benchmark in 3 minutes," then a live leaderboard.
5. **Robot stress test.** A robot policy runs in simulation. An adversarial swarm uses world models to generate harder scenes (clutter, bad lighting, slippery objects) to break it.
   - **Wow:** a heatmap of failure conditions. Fix the policy, re-run, and the heatmap shrinks.
6. **Pixel Judge for visual coding.** A swarm rebuilds a UI from one screenshot; Argon renders each attempt, compares it to the original, and the best survives.
   - **Wow:** the similarity score climbs live, 41 → 72 → 96.
7. **Agent courtroom.** When swarm agents disagree, prosecutor, defense and jury agents argue it out with timestamped evidence.
   - **Wow:** a verdict with receipts. The weakest of the seven.

**Pick at the time:** #1 Swarm QA (the judge's own site is the strongest wow). #2 is the cleaner fit for a project purely about evaluating swarms.
