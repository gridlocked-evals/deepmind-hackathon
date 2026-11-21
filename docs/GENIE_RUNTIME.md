# Genie session runtime

Issue #9 adds a backend-owned WebSocket session independently of the city UI.
It creates `genie-4` through Interactions v1beta, mints an interaction-scoped
token, connects using that token, and sends one `world_setup` with text and one
exact 16:9 reference image. The project key and provider token stay in Python.
The backend relays JPEG frames to authorized clients and records every received
frame before publishing it. Audio is not relayed in this version.

Protocol references checked November 21, 2026: [Genie lifecycle and controls](https://aistudio.google.com/docs/genie),
[model constraints](https://aistudio.google.com/docs/models/genie-4), and
[pricing](https://aistudio.google.com/docs/pricing#genie-4).

## Development verification

No paid calls:

```powershell
.\.venv\Scripts\python -m backend.worlds.spike
```

One development recording, capped at 20 seconds from creation dispatch plus the
configured cleanup allowance, with at most a $3 local reservation:

```powershell
.\.venv\Scripts\python -m backend.worlds.spike --live
```

This uses the existing backend Gemini key. It sends a 500 ms W press, releases
it, then sends one adversary text prompt. It marks the recording development-only;
it cannot publish image acceptance. The initial reference is a synthetic diagram,
not a real-camera image. See [the live verification record](GENIE_RUNTIME_VERIFICATION.md).

## Trusted accepted-image handoff

The production API accepts an `accepted_reference` hash, never uploaded media or
a client assertion that the gate accepted it. Trusted Python code in the #8
image gate publishes it with `gates.publish_accepted`, exposed as the operator-token route
`POST /api/worlds/accepted-references` with `{referenceImageId, gateDecisionId}`. The route
accepts only IDs: the gate code itself loads and re-verifies the accepted image and gate record.
When the manager has the gate store (as in `backend/main.py`), creation also claims the gate
and binds it to this session, so one accepted gate admits exactly one session.
`AcceptedReference` is a narrow integration contract, not a replacement for the
domain schema work in #2. It binds:

- episode, source, capture and reference IDs;
- SHA-256 of the exact encoded image and the exact gate artifact;
- gate decision ID, MIME type, schema version and `decision: accept`.

Publication requires matching hashes and a decodable image with exact 16:9 pixel
dimensions. Creation reloads and revalidates all bytes and matches the submission
or demo context. Missing, modified, rejected or unknown records fail before any
paid call. No HTTP endpoint accepts client-supplied images or gate claims. Publishing is a trusted backend
operation: the runtime does not independently judge the image or authenticate a
malicious writer with filesystem access. `backend/test_gate_world.py` covers the
end-to-end path; its fake judge is not live acceptance evidence.

## HTTP and browser protocol

Configure a separate random `WORLD_API_TOKEN` of at least 32 characters in the
backend environment to enable creation/status/stop routes. It is an operator
credential; do not embed it or the Gemini key in frontend source. Missing token
configuration leaves these routes disabled. Camera routes remain public.

`POST /api/worlds` requires `Authorization: Bearer <WORLD_API_TOKEN>`:

```json
{
  "accepted_reference": "<64-character SHA-256 from the trusted gate store>",
  "context": {
    "episode_id": "episode-1", "source_id": "camera-1", "capture_id": "capture-1",
    "prompt_id": "world-v1", "purpose": "submission"
  },
  "prompt": "Explore the accepted redesigned street.",
  "seconds": 15,
  "max_usd": "2.5"
}
```

The response contains a session ID, duration limit, WebSocket path and separate
one-use `human`, `defender`, and `adversary` capabilities. Give each client only
its capability. Connect within 10 seconds. The first WebSocket text message is
`{"role":"human","token":"<human capability>"}`; tokens are never URL parameters.
Only human attachment starts paid work. An abandoned creation expires without
dispatch. Other roles can attach in that initial window. Reconnection is not
supported; disconnect terminates the session. Browser origins are restricted to
localhost/127.0.0.1 on ports 5173 and 8000. Vite proxies WebSockets under `/api`.

After authentication, send `{"kind":"heartbeat"}` at least once a second for
each attached role. There is a three-second server inactivity deadline. Commands:

| Message | Authority / bound |
| --- | --- |
| `{"kind":"key","owner":"human","key":"key_w","hold_ms":250}` | Current key owner only; at most 500 ms by default. |
| `{"kind":"takeover","owner":"defender"}` | Human only; releases all held keys before changing owner. Use `human` to take back control. |
| `{"kind":"release"}` | Human or current key owner; releases all held keys. |
| `{"kind":"steer","owner":"adversary","text":"A bus enters from the right."}` | Adversary only; one prompt per two seconds, 1,000 characters per prompt and 8,000 UTF-8 bytes total. |
| `{"kind":"stop"}` or `{"kind":"emergency_stop"}` | Any attached role; starts release and explicit termination. |

Key inputs are W/A/S/D (`key_w`, etc.) and camera arrow keys (`arrow_left`, etc.).
They represent visual navigation, not calibrated acceleration/braking. Repeated
key-down cannot extend a held key. Key expiry runs independently of incoming
frames. Human takeover does not transfer ownership of text steering. Malformed,
unauthorized, stale-frame or excessive commands end control. Defender queues
contain received pixels/status only, never adversary text or attack plans.

The relay sends `kind: frame` with zero-based `frame_index`, receipt timestamp,
`mime_type: image/jpeg`, and base64 `data`. Slow clients may skip display frames;
every frame received by the backend remains in the recording. Never let an older
asynchronous image decode replace a newer frame. Terminal `kind: status` includes
termination outcome and any typed error. `GET /api/worlds/{id}` and
`POST /api/worlds/{id}/stop` use operator authentication. Completed sessions may
leave the in-memory registry when another is created; their evidence stays on disk.

## Duration, money and termination

Default runtime limits are 30 seconds and $3 per session; requests can lower both.
The dollar allowance reserves input plus video tokens for the full permitted
duration and cleanup, and shortens the duration when necessary. The existing
gateway's persistent stage/global budgets, demo reserve and concurrency admission
also apply. There is currently one admitted Genie session at a time, inherited
from #3; the four-key pool is separate unfinished work.

The runtime deadline begins before the create request, including connection/setup
latency. Cleanup has its own finite allowance (five seconds by default). Stop,
disconnect, heartbeat loss, no-frame timeout, shutdown, control failure and duration
expiry all attempt key release followed by `complete()`. The sole receiver stays
alive to record trailing frames and the terminal acknowledgment. If that fails,
HTTP cancellation uses the remaining cleanup allowance. Unconfirmed termination
keeps its reservation and blocks new Genie work. Exiting the SDK context alone
does not count as termination.

A lifespan watchdog retries cancellation of known abandoned interactions after
their ledger leases expire. It does not cancel another live worker's lease.
An ambiguous creation without an interaction ID cannot be cancelled by this
runtime and remains visibly unresolved in `/api/models/status`. The existing
`backend.gateway.smoke --cancel-world LOCAL_REQUEST_ID` recovery path is available
for known IDs. A shutdown/power loss can delay cleanup until the backend restarts;
this is not a provider-side hard billing cap. Unknown usage remains conservatively
reserved. Use one API worker (or sticky routing) because live socket ownership is
in memory; durable accounting remains shared across workers.

## Evidence and remaining integration

`.gateway/worlds/<session-id>/` holds `session.json`, append-only `events.jsonl`,
and `frames/000000.jpg`, etc. Logs bind session/reference hashes, gate provenance,
key and text events, frame indices, receipt times, control owner and terminal
outcome. Frame index is the shared received-media clock, not a simulator timestep.
The gateway's SQLite ledger retains request IDs, usage and budget accounting.
All runtime files remain Git-ignored. There are no deterministic seeds, restartable
world state or calibrated steering claims. Pause/resume is unsupported; no pause
message is invented.

The real accepted-image pipeline (#8), generated-world UI, Hydrogen controller
(#10), multi-key quota validation and larger control/steering measurements remain
integration work. Keep #9 open until its integrated acceptance is verified.
