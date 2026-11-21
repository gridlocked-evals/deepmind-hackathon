# Genie runtime verification — November 21, 2026

One bounded synthetic development session exercised the new runtime between
12:17:06 and 12:17:24 America/New_York. It created an interaction, minted scoped
authorization, connected through the Python SDK, sent one text-plus-image setup,
recorded all received frames, sent/released W, sent one text event and explicitly
completed. This was not a real accepted-reference submission or a driving-policy
evaluation.

| Measurement | Observed result |
| --- | --- |
| Local session ID | `1e127751-b5e4-4a89-96ee-0f7d71a43d17` |
| Gateway request ID | `78ecdac7-0429-4f11-b1ac-68a946862248` |
| Model / transport | `genie-4` / SDK WebSocket with interaction-scoped token |
| Received frames | 208, indexed 0–207; all saved as 1280×720 JPEGs |
| First frame | 7,269.732 ms after local session creation |
| Key down | Sent after frame 21, at 8,298.134 ms |
| Key up | Sent after frame 33, at 8,822.583 ms; observed hold 524.449 ms for a requested 500 ms |
| Text steering | Sent after frame 76, at 10,304.562 ms |
| Completion request / acknowledgment | 15,393.714 / 17,641.628 ms |
| Terminal outcome | `interaction.completed`; no held keys or unresolved world requests |
| Reported input / output tokens | 145 / 49,920 (208 × 240 output tokens) |
| Local estimated charge | $0.873818; not a provider invoice |

The deadline was 20 seconds plus at most five seconds for cleanup. Its conservative
reservation was $2.569152. The recording finished within both bounds. The server
continued receiving and recording 46 frames after `complete()` was sent, until
the terminal acknowledgment arrived. Local scheduling and transport introduce
key-release delay; a requested hold is not an exact physical control duration.

## Visual observations indexed by received frames

The following are assistant visual comparisons of sampled frames, not exact
onset detection, a blinded human study, or a causal controlled experiment.

- **Key response:** frame 41 still has approximately the pre-action viewpoint;
  frame 53 has a clear viewpoint/road-marking shift consistent with forward visual
  navigation. The reviewed interval is 20–32 received frames after the key event,
  or approximately 706–936 ms by receipt timestamps. This bounds the reviewed
  change, not the precise onset or a population latency percentile.
- **Text response:** the bright blue bus is absent in frame 120 and visible from
  the right in frame 135. That is a reviewed interval of 44–59 received frames
  after steering, or approximately 1,906–3,407 ms. It crosses ahead and exits.
  One of one prompts rendered the requested object; the instruction to remain
  visible was not satisfied. This is not a general steering-success estimate.
- **No-key behavior:** W was released after frame 33. Viewpoint change continued
  in later frames, then was approximately stable in sampled frames from 71 onward
  while the bus moved. Release is the bounded default input for #10, but does not
  establish calibrated braking or an immediate visual stop.
- **Pause:** not live-tested; the documented protocol has no resumable pause.
  The runtime provides stop/complete, not a fabricated pause command. Socket
  detachment is not used as a billing stop.

The startup fixture is a simple road diagram. The model transformed it into a
dashboard view during initialization; this does not demonstrate preservation of
an accepted real street edit. Key and steering responses need broader testing
with the actual accepted references before policy/evaluation claims.

## Retained evidence

Private evidence is under
`.gateway/worlds/1e127751-b5e4-4a89-96ee-0f7d71a43d17/`:

- `frames/000000.jpg` through `frames/000207.jpg`;
- `events.jsonl`, SHA-256 `52492b5cbb761ea49fb2e68ebb5cb246382b917ebfa95c5a940e1e7384dca7c2`;
- `visual-review.json`, SHA-256 `df3edfd99511786858de9f30641dcbe4a4cf07058109dd808edeea622d1f21d9`;
- `session.json`, plus the existing gateway ledger/request artifacts.

The visual review binds the sampled frame hashes and receipt-lag calculations.
Runtime evidence, credentials and scoped tokens are not committed. Project
concurrency/day limits and actual invoice settlement remain unverified; this
single session demonstrates access and a terminal protocol acknowledgment.

Offline tests also exercise key expiry, per-channel ownership, takeover,
malformed controls, heartbeat/frame/duration timeouts, disconnect, provider
failure, quota rejection, missing frames, budget admission, trusted reference
tampering, expired-lease recovery, browser capability/origin restrictions, disk
failure and caller cancellation during cleanup. These injected failures do not
claim live provider behavior for every failure case.

Issue #9 remains open for the accepted-image pipeline (#8), UI integration and
broader project/control measurements.
