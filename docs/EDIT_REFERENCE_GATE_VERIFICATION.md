# Edit/reference/gate live verification — November 21, 2026

These are development runs through `python -m backend.pipeline_live` between 12:37 and 12:42 p.m. America/New_York. They used one real NYC DOT snapshot camera: `8afc5cd6-…`, "WBB-20 @ Above Bedford Ave (Berry St)", a 352×240 JPEG. They are not submission evidence or a locked evaluation set. The Hydrogen step was the development anchor stand-in, not issue #5's extractor. Artifacts are in the ignored `var/pipeline/` directory and are not committed.

## Run 3 (after fixes)

| Step | Result | Latency | Upper-bound cost |
| --- | --- | ---: | ---: |
| Hydrogen anchors (stand-in) | 12 anchors: 4 elevated travel lanes, 1 sidewalk, 7 building facades. No crosswalk visible. | 19.5 s | $0.031 |
| Carbon: "Convert the rightmost travel lane into a protected bike lane separated by planters." | `compiled`: modify `travel_lane` → `bike_lane`, plus add `planter`. Passed every validator. | 27.2 s | $0.024 |
| Nano Banana `photoreal` | raw 1376×768 JPEG, normalized to 1280×720 PNG | 11.7 s | $0.053 |
| Decisions on photoreal | **accept**: viewpoint 0.90, anchors 0.90, requested change 0.88, unambiguous 0.81 | 1.6 s | $0.0003 |
| Nano Banana `cel_shaded` | raw 1376×768 JPEG, normalized to 1280×720 PNG | 14.0 s | $0.068 |
| Decisions on cel-shaded | **insufficient_evidence**: unambiguous 0.78, inside the provisional (0.3, 0.8) band. World creation stays blocked; the judge was not re-asked. | 1.4 s | $0.0003 |
| Carbon: "Make it nicer for bikes somehow." | `needs_clarification`: "Which lane should be converted … leftmost or rightmost elevated travel lane … planters or a raised median buffer?" | 17.5 s | $0.017 |

On visual inspection, the photoreal reference keeps the bridge viewpoint, graffiti wall and skyline. It shows a green lane with bike symbols and a planter row on the right lane. The cel-shaded version widens the framing slightly, which plausibly explains the lower score. The source is 4:3-ish (352×240), so the edges of both 16:9 outputs are generated interpretation, as the attempt records state.

Run 2 also showed a conflicting request being refused. "Add a bike lane, and also remove the crosswalk so bikes go faster" was rejected by Carbon: "Removing a crosswalk violates the protected_bike_lane preset permissions …". Gateway-estimated spend for all three runs was $0.72, an upper bound and not an invoice.

## Failures found and fixed

1. **Schema too complex.** Both Carbon calls in run 1 returned HTTP 400, "The specified schema produces a constraint that has too many states for serving." Fix: `wire_schema()` sends Carbon a schema without length/range bounds, and full Pydantic validation still runs on the response.
2. **Missing reason.** In run 2, Carbon returned `needs_clarification` with no reason, and the validator turned it into a visible rejection. Fix: `reason` is now a required string, and the prompt says it is always required.
3. **Harness bounds too tight.** The Hydrogen stand-in exceeded the harness's own anchor bounds (`invalid_output`). Fix: wider bounds, the wire schema, and one retry. This affected the harness only.
4. **Transient Carbon timeout.** One Carbon capability probe timed out at the gateway's 45 s limit, and the immediate retry succeeded in 9.6 s. Compilation took 17–27 s on real evidence, so the timeout is close to the observed range.

## Not yet established

- **Thresholds:** they are provisional. No blinded human labels exist yet, so false acceptance and abstention coverage are **unmeasured**. Use `backend.gate_review export/score/calibrate`.
- **Other presets:** busway and plaza have been exercised only offline. No crosswalk-bearing camera has gone through the live gate yet.
- **Retry paths:** the re-edit, retry-exhaustion and timeout paths are covered by offline tests, not by live runs.
- **Hydrogen evidence:** real #5 evidence (timestamped observations from HLS video) has not been integrated.
