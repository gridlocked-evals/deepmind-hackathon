# 🏆 FINAL IDEA: Crash Test NYC, Remastered

**The coolest version. Builds on [08-crash-test-nyc-mayor-mode.md](08-crash-test-nyc-mayor-mode.md) and adds one step: remaster the camera frame with Nano Banana before Genie 4 turns it into a world.**

**One line:** pick any live NYC traffic camera, remaster it into a clean street-level scene in any graphics style, redesign the street as the mayor, then play it in Genie 4 while a robotaxi agent tries to drive it safely.

## Why the remaster step
The raw DOT frame is a weak seed for Genie:
- **Low-res:** 352×240.
- **Wrong shape:** Genie needs 16:9.
- **Wrong viewpoint:** a high pole camera, not a driver's or pedestrian's view.
- **Often dark:** night, glare, rain on the lens.

Nano Banana fixes all of that in one image edit. It also unlocks three things:
1. **Graphics are a dial.** It doesn't have to be realistic. The same corner can be photoreal, video-game style, cel-shaded, low-poly, retro 16-bit, claymation or voxel.
2. **The mayor's order goes into the image, not just into Genie's text steering.** An image edit is far more controllable than steering a live world. Draw the busway or the plaza into the seed, and Genie animates a street that already has it.
3. **Stylized scenes are safer with Genie.** The Genie docs recommend stylized art. Stylizing blurs real people, plates and storefront logos, which can otherwise block `world_setup` or end a stream.

## The pipeline
1. **Pick a corner.** A live NYC DOT camera frame, chosen by the judge.
2. **Remaster (Nano Banana).** Upscale to 1280×720, re-imagine it from street level, apply the chosen graphics style, and apply the mayor's order. The result is the **"Mayor's postcard."**
3. **Check before spending (Decisions API).** About $0.10/s of Genie only gets spent once the postcard passes three questions:
   - Is it still recognizably this corner? (score vs the raw frame)
   - Did the requested change appear? (yes/no)
   - Is it a clean 16:9 street view? (yes/no)

   If it fails, regenerate the image. That's cheap.
4. **Play (Genie 4).** The postcard becomes a walkable or drivable world. Mayor Mode: the judge walks or drives it.
5. **Robotaxi Test (Hydrogen agent + Decisions API judge).** The agent drives the redesigned street, the judge scores every second, then you fix the agent and re-run.

## The wow moments
- **The triple split screen:** live camera, then the remastered postcard, then the playable world, all for the same corner from 30 seconds ago.
- **The graphics dial:** flip the same street from photoreal to retro 16-bit to claymation, and walk each one.
- **The mayor's before/after slider:** drag a slider across the postcard to show today's 7th Ave vs. the busway version, then step into it.
- **Robotaxi in every style:** does the agent still drive safely when the world is cartoon or low-poly? That's a fun robustness test.

## Proof (the 25%)
The remaster step gives us extra baselines to beat, which suits the 5 Sigma track:
- **Raw seed vs remastered seed:** seed Genie with the raw camera frame vs the postcard. Compare world quality, how long the world stays coherent, and the robotaxi agent's violation rate.
- **Text steering vs image edit:** apply the mayor's order through Genie text steering vs through the Nano Banana edit. Measure how often the change actually appears (Decisions API yes/no).
- **Style robustness:** the agent's safety score across graphics styles. A big drop in one style is an honest "here's where it breaks."
- **Our agent vs baseline:** the Argon planner + Hydrogen driver + safety check vs one model call per frame, on unseen cameras.

## Model jobs

| Model | Job |
|---|---|
| **Nano Banana** (Gemini image model) | Remaster: upscale, change viewpoint, apply graphics style, draw the mayor's order |
| **Gemini 4 Argon** | Turns the mayor's order and the camera frame into the image-edit prompt, the Genie world prompt and the scenario |
| **Gemini 4 Hydrogen** | The fast robotaxi driving agent |
| **Decisions API** | The pre-Genie checks and the live driving judge |
| **Genie 4** | The playable world |

The rules need at least two **Gemini 4** models with clear jobs. Argon and Hydrogen cover that; Nano Banana is an extra on top. Check the exact Nano Banana model ID in AI Studio today.

## Risks
- **The remaster can stop looking like the real corner,** especially with heavy styles. The Decisions API place check catches it; show the score on screen.
- **Changing to street level means Nano Banana invents what the pole camera couldn't see.** Say so honestly: it's a believable re-imagining, not a survey.
- **No brand or IP names in style prompts.** Say "open-world crime game style," not a game's title. Say "toy brick," not a toy brand. Genie blocks identifiable brand IP.
- **Still no real people.** Mayor Mode stays generic: no Mamdani name or face in any generated image or world.
- **Time:** the remaster adds one image call per run, a few seconds. Cache postcards for the demo's backup run.

## Build order today (no code yet; for planning)
1. **Spike:** raw frame → Nano Banana postcard → Genie world → W moves forward. Try one realistic style and one game style.
2. **Decisions API checks** on the postcards.
3. **Mayor's order** in the image edit, plus the before/after slider.
4. **Robotaxi agent + live judge.**
5. **Eval sweep:** raw vs remastered, text vs image edit, style robustness.
6. **Demo polish:** triple split screen, graphics dial, recorded backup.
