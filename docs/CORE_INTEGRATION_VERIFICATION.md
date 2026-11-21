# Core integration verification

Verified November 21, 2026, while integrating `codex/element119-core` with the team's canonical `element119.v1` pipeline on main.

## Capture acceptance (#4)

The unified recorder retains the main branch's locking for pin/eviction races and adds mandatory bounded decoder audits, manifest integrity verification, conservative pin recovery and prefix-only replay. Both visible and held-out original segments must decode; actual decoded aggregate duration cannot exceed 120 seconds. Missing decoder tools, corrupt/truncated segments and changed buffer selections are rejected. Source exposure time remains distinct from server receipt time.

An isolated server with model calls disabled recorded the real 511NY Vestry camera (`511ny-32998`) at its registered coordinates. Capture `cap_20261121T192926-6101fde8` retained a 15.016-second source interval: the permitted prefix was 10.01 seconds, 150 frames and 344,792 bytes; the held-out suffix was 5.006 seconds and 176,908 bytes. The local replay playlist exposed exactly two prefix segments, while a holdout HTTP request returned 404. The source clock was explicitly unknown.

A real DOT snapshot (`1b3248f9-f26e-4f36-943f-b7a7731595fb`) produced capture `cap_20261121T192944-1d0bef87`: one frame, 15,929 bytes, zero duration and the `snapshot_only`, `no_motion_evidence` and `source_clock_unknown` labels.

Automated regression coverage includes actual 12-second MPEG-TS decoding beyond 68 frames, decoded caps including held-out media, malformed held-out bytes, recorder history revisions, stopping without dropping the buffer, modality distinctions, source/scene mismatch, immutable retries, missing/corrupt pin journals, restart preservation, path/hash corruption and both pin/eviction races. Test media and provider fixtures are synthetic and are not counted as live model acceptance.

## Evidence and pipeline integration (#5, #12)

The backend and UI use one capture/observation contract. Operator-authenticated extraction publishes canonical batches after source, frame, time and event validation. Successful durable artifacts are bound to their gateway records; all-abstention batches and rejected Carbon edits do not qualify as meaningful submission outputs. Camera source IDs are preserved through Carbon, references, gates and world handoff. Public arbitrary observation registration was removed.

Human annotation tooling binds reviews to exact extraction reports and frame hashes, keeps empty templates unscored, and includes failures and abstentions in reports. This change does not manufacture human labels. Real clip count/event/anchor annotation remains outstanding for #5.

Genie is optional and defaults off. Capture, observations, edit compilation and reference/gate review remain independent of world generation. Retained world results report generated provenance and explicitly mark temporal judgments as not run when the runtime has no judge integration. Full #12 acceptance still requires the live Hydrogen driving loop and temporal judge integration/verification.

All model checks in this integration use isolated fixtures or disabled providers. No new paid model requests or credit-balance claims are made.
