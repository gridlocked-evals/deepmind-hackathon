# Crash Test NYC, Remastered / Element 119

Approved submission scope, November 21, 2026. [Issue tracker](https://github.com/Paradox560/deepmind-hackathon/issues/1).

## Product and evidence boundary

Select a real NYC camera and capture at most **120 seconds of original evidence**. Hydrogen extracts evidence; Carbon compiles a mayor edit; Nano Banana produces a reference image; Decisions accepts or rejects that reference; Genie creates a navigable visual world. Human or Hydrogen navigation is supervised by temporal Decisions checks. Evaluate the pipeline on unseen real camera inputs with a simple baseline and retain failures and unknown outcomes.

The qualifying Gemini 4 pair is **Hydrogen and Carbon**. Nano Banana, Decisions, and Genie have separate required roles and cannot replace either member of that pair. A successful episode must contain meaningful evidence extraction and edit compilation, not empty calls made to satisfy a checklist.

The submission uses normalized image-space anchors/regions and semantic permissions. It does not claim measured lane widths, physically feasible routes, calibrated traffic dynamics, or real-world safety. Snapshot evidence cannot establish trajectories or flow rates. Genie session/reference IDs identify stochastic visual generation; they are not simulator seeds or reproducible branch checkpoints. Restarting a Genie session is not an exact reset.

## Starting point and attribution

Baseline: [`eff00a81b01294b9240c629c371ae446055e1841`](https://github.com/Paradox560/deepmind-hackathon/commit/eff00a81b01294b9240c629c371ae446055e1841), recorded before this implementation tranche. The baseline already contains the NYC geographic map, authored Vestry traffic demonstration, camera selection, DOT snapshot directory, and bounded 511NY HLS proxy. Its deterministic demo is illustrative, not camera reconstruction.

The repository is already public. Pharos attribution and its source revision remain in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Existing UI and research notes are baseline work and must be identified as such. Public availability and attribution do **not** establish an exception to any event-only or reuse rule.

**Rule verification remains open:** the [official event listing](https://cerebralvalley.ai/events/new-york) identifies the event but does not supply event-only/reuse terms; the detail page could not be retrieved on November 21. Record the organizers' actual wording before claiming the baseline UI was event-built or submission-eligible. Do not invent approval.

## Implemented foundation versus pending integration

This tranche adds versioned Python/TypeScript artifact contracts and a shared validation fixture, a server-only model gateway with credential profiles and bounded usage, and bounded capture/storage primitives. [Contracts](docs/CONTRACTS.md), [gateway](docs/MODEL_GATEWAY.md), and [capture](docs/CAPTURE_API.md) document their exact interfaces and limitations.

Automated fixtures and mocked provider tests validate implementation behavior; they do not prove account access, quotas, live capture quality, or model quality. Required live model smoke records, real-media acceptance, the end-to-end pipeline, Mayor Mode UI, and held-out evaluation remain integration work. The existing UI still displays its authored demonstration.

## Four work lanes

| Lane | Ownership | Immediate interface |
| --- | --- | --- |
| A — evidence/capture (`Paradox560`) | Source registry, immutable capture, Hydrogen observations | Capture IDs/hashes and `ObservationBatch` |
| B — world/control (`amrevr`) | Genie lifecycle, input actions, Hydrogen navigation; later H100 work in #23 | `WorldSession`, `NavigationEvent` |
| C — reasoning/evaluation (`tasosger`) | Contracts, gateway, Carbon edits/repair, Decisions gates, evaluation | `StreetEdit`, `GateDecision`, `EvaluationResult` |
| D — images/UI (`Jalil-g`) | Nano Banana image attempts, comparisons, camera/time selector, Mayor Mode | `ReferenceImage` and validated artifact read models |

Each lane can work against versioned fixtures immediately. Shared schemas define ownership, identifiers, time domains, provenance, explicit unknowns, and route proposals. No lane waits for SUMO.

## Core sequence

| Issues | Deliverable | Acceptance evidence |
| --- | --- | --- |
| Contract task (original #2), [#3](https://github.com/Paradox560/deepmind-hackathon/issues/3) | Frozen contracts and model gateway | Cross-language fixture; invalid references rejected; real service traces with actual limits |
| [#4](https://github.com/Paradox560/deepmind-hackathon/issues/4), [#5](https://github.com/Paradox560/deepmind-hackathon/issues/5) | Bounded capture and Hydrogen extraction | Immutable source bytes; truthful history bounds; clip and snapshot observations with evidence links |
| [#6](https://github.com/Paradox560/deepmind-hackathon/issues/6) | Carbon protected-bike-lane compiler | Typed edits, preserved anchors, explicit unknown geometry, rejected conflicting requests |
| [#7](https://github.com/Paradox560/deepmind-hackathon/issues/7), [#8](https://github.com/Paradox560/deepmind-hackathon/issues/8) | Nano Banana reference and Decisions gate | Preserved attempts; accepted 16:9 image; judgments actually block world creation |
| [#9](https://github.com/Paradox560/deepmind-hackathon/issues/9), [#10](https://github.com/Paradox560/deepmind-hackathon/issues/10), [#11](https://github.com/Paradox560/deepmind-hackathon/issues/11) | Genie session and supervised navigation | Generated frames, bounded actions, unknown coverage, explicit completion and cost limits |
| [#12](https://github.com/Paradox560/deepmind-hackathon/issues/12) | Fresh-camera Mayor Mode | Real source selection, source/reference comparison, playable session and honest service state |
| [#13](https://github.com/Paradox560/deepmind-hackathon/issues/13), [#14](https://github.com/Paradox560/deepmind-hackathon/issues/14) | Locked evaluation and bounded repair | Unseen real inputs, equal-budget baseline, retained failures, measured boundary and repair effect |
| [#15](https://github.com/Paradox560/deepmind-hackathon/issues/15) | Integrated submission | Reproducible evidence package with actual calls/results and remaining gaps |

Planning targets: a real media-to-world slice by 90 minutes, visual control by hour three, frozen inputs and baseline by hour four, stress/repair by hour 5.5, held-out results by hour 6.5, integration by hour eight. These are targets, not observed timings or completed milestones.

The original contract issue #2 was readable when implementation began but was no longer available on GitHub at assignment time. Its requirements remain implemented and documented here; no replacement issue or closure is implied.

## Runtime invariants

- Preserve exact source bytes, hashes, source/capture identity, exposure uncertainty, and server receipt times. Model retries reuse immutable media; retries never recapture a different moment.
- Only advertise history that is actually buffered. Bound storage and retain media pinned by jobs. Forecast prefixes and sealed suffixes are separate files; inference receives only permitted prefix media.
- Preserve observed, derived, assumed, inserted, and generated provenance. Unknown visibility or occlusion does not imply actor disappearance.
- Keep credentials on the server. Gemini's standard model methods, typed Decisions judgments, and Genie Interactions sessions have distinct transports. Show configured, unverified, connected, stale, and unavailable honestly.
- Bound concurrency, retry count, timeouts, tokens, and estimated dollars; protect a demo reserve. Record request/result hashes, exact model/prompt/config identifiers, latency, usage, and outcome, with no secrets in logs.
- Stop Genie server generation explicitly. Detaching a transport is not completion. Do not auto-retry ambiguous session creation and accidentally start another billable world.
- Decisions output changes acceptance and navigation/termination behavior. Confidence, observable coverage, abstentions, and unknown outcomes remain visible.
- Evaluate the full attempts denominator and a simple fair baseline on real independent inputs. Preserve failed/unknown attempts, frozen test identities, sample counts, and measured operating limits.

## Later milestones

**P1, issues #16–#21:** camera calibration and metric ScenarioIR; actor existence/visibility and boundary lifecycle; signal-group state; reactive SUMO rollouts with frozen branches and policy isolation; uncertain fitting on held-out traffic; and reproducible counterexample/repair evaluation.

**P2, issues #22–#23:** a SceneDiffuser++-inspired learning experiment and an H100 state-conditioned Cosmos spike after metric validation. The [SceneDiffuser++ reference](https://arxiv.org/html/2506.21976v1) motivates actor/signal lifecycle and planner/world isolation; it does not supply a roadside-camera generator or establish city-scale learning from one short clip.

SUMO, Cosmos, diffusion training, extra models, optional voice, and additional visual styles are off the submission critical path. The previous SUMO-first proposal is preserved in [baseline Git history](https://github.com/Paradox560/deepmind-hackathon/blob/eff00a81b01294b9240c629c371ae446055e1841/GOTHAM_FORGE_PLAN.md).
