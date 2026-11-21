# Element 119 model gateway

`backend/gateway` provides server-only, bounded calls for the five required model IDs. No model request runs on application startup. `GET /api/models/status` reports configuration, verification state and reserved usage without exposing credentials.

## Credentials

Place the two Gemini AI Studio keys in the ignored repository-root `.env`:

```dotenv
GEMINI_API_KEY=your_tier_3_key
WORKSHOP_API_KEY=your_workshop_key
GEMINI_KEY_PROFILE=primary
```

`primary` selects `GEMINI_API_KEY`; `workshop` selects `WORKSHOP_API_KEY`. All five adapters use the selected credential. There is no fallback between projects. A missing selected key blocks requests. Restart the backend after changing the profile. Credentials never use a `VITE_` prefix and never enter the frontend.

## API surfaces

| Stage | Exact model | Transport |
| --- | --- | --- |
| Observation/navigation | `gemini-4-hydrogen` | Stateless Interactions JSON |
| Edit compilation/repair | `gemini-4-carbon` | Stateless Interactions JSON |
| Reference image | `gemini-nano-banana-2.1` | Stateless Interactions JSON |
| Typed judgment | `gemini-decision-1` | Decisions JSON, validated typed answers |
| World capability probe | `genie-4` | Interactions creation plus SDK WebSocket session |

The current [Interactions documentation](https://aistudio.google.com/docs/interactions-overview) recommends Interactions for new text/image integrations. JSON calls use the documented `2026-05-20` API revision and `store=false`. [Decisions](https://aistudio.google.com/docs/decisions-api) has its own endpoint and typed result semantics. [Genie](https://aistudio.google.com/docs/genie) uses a separate connect/send/receive/complete lifecycle; disconnecting alone does not stop generation.

The Genie adapter is a capability probe, not the playable session manager from issue #9. It creates a world, receives one actual JPEG frame, hashes it, requests completion, and waits for the terminal acknowledgment. Deadline, cancellation, stream error and connect failure trigger cleanup. If socket cleanup fails, the installed SDK's `interactions.cancel(id)` provides a separate server-side cancellation path. An unconfirmed termination is recorded with the remote interaction ID and prevents further Genie sessions. Resolve that remote interaction before reconciling its ledger entry; deleting the ledger does not stop provider billing.

## Calling a stage

```python
result = await gateway.run(
    "hydrogen",
    {"input": [{"type": "text", "text": "Describe visible street evidence."},
               {"type": "image", "mime_type": "image/jpeg", "data": encoded_bytes}]},
    prompt_id="observe-v1",
    config_id="capture-inspection-v1",
    reserve_tokens=8192,
    reserve_usd=1,
    source_id="511ny-32998",
    capture_id="capture-example",
    original_media_sha256=(original_sha256,),
)
```

The caller retains `result.output` as its artifact and validates the stage-specific contract. The gateway returns a validated shared `ModelTrace`. `requestId` is locally assigned; `providerRequestId` contains the provider identifier when supplied and is otherwise explicitly null. `inputSha256` hashes the exact canonical request bytes, while `originalMediaSha256` preserves caller-provided original-media provenance separately. UTC timestamps end in `Z`.

Media must be inline immutable bytes. URL/file references, tools, background jobs, streaming JSON requests and previous interaction references are rejected. Retries resend identical serialized bytes; they never capture another camera moment. Hydrogen/Carbon capability probes do not substitute for meaningful observation/edit outputs on a real submission episode.

## Usage admission and logging

The SQLite ledger at `MODEL_LEDGER_PATH` survives restart. Relative paths resolve against the repository root. Each attempt reserves tokens and dollars in a transaction before dispatch. Retries, unknown outcomes, timeouts and failed requests retain reservations. Actual token overages increase the ledger charge. The ledger records model, prompt/config IDs, input/output hashes, source/capture references, timestamps, latency, outcome, usage when reported and provider request ID. It never stores prompts, media bytes, provider error bodies, API keys or raw output. A private credential digest scopes verification to the selected key; it is excluded from API results.

Dollar reservations are **caller-declared estimates, not measured provider prices or hard billing ceilings**. The gateway prevents new work that exceeds configured reservations; it cannot guarantee a provider invoice cap. Account-level billing controls and correct conservative per-request estimates are still needed. Token reservations are admission limits, with provider overages preserved; generation output is capped separately. Missing provider token usage remains null, never zero. Project quota maxima remain unmeasured until deliberately tested; a successful smoke call only verifies that capability for that project at that time.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `MODEL_LEDGER_PATH` | `data/gateway.sqlite3` | Persistent private attempt ledger |
| `MODEL_MAX_CONCURRENCY` | `2` | In-flight calls per server process |
| `MODEL_MAX_RETRIES` | `1` | Additional attempts, maximum 3; Genie never retries creation |
| `MODEL_TIMEOUT_SECONDS` | `30` | Per-attempt deadline; bounded Genie cleanup may extend it |
| `MODEL_TOTAL_BUDGET_USD` | `20` | Total admission reservation limit |
| `MODEL_DEMO_RESERVE_USD` | `5` | Reserved for explicit `demo=True` calls |
| `MODEL_STAGE_BUDGET_USD` | `5` | Limit for each of the five service stages |
| `MODEL_STAGE_TOKEN_BUDGET` | `100000` | Token admission limit per service stage |
| `MODEL_MAX_REQUEST_TOKENS` | `16384` | Largest allowed single-attempt token reservation |
| `MODEL_MAX_REQUEST_USD` | `4` | Largest allowed single-attempt dollar reservation |

Use one API worker for the configured concurrency cap. SQLite coordinates spending admission across processes, but the concurrency semaphore is process-local. Existing evidence is never removed when a budget stops new work. The ledger budget is intentionally cumulative until an operator archives/reconciles a run; restart does not refill it.

State meanings: `configured` means the chosen key is present but untested; `unverified` means a request began without a known final result; `connected` requires a recent successful response from that same key; `stale` means its success is older than five minutes; `unavailable` means missing credentials or a failed last attempt. Provider failures return stable typed messages rather than raw exceptions.

## Capability smoke tests

```sh
.venv/bin/python -m backend.smoke_models --live --service hydrogen
.venv/bin/python -m backend.smoke_models --live --service all
```

`--live` is required because the command makes billable calls. `--use-demo-reserve` explicitly admits work against the protected reserve. CLI output contains sanitized trace/usage/status JSON; provider output and credentials are not printed. The all-services probe reserves $8 across the five stages by default, plus any inference retries. This is an admission reservation, not a quoted or measured charge. Genie requests one frame and immediately terminates the session. Missing keys fail visibly without making requests.

Offline validation: `python -m pytest backend/test_gateway.py -q` covers provider payloads, immutable retries, typed failures, timeouts, concurrency, budgets/restart, key rotation, trace schema compatibility, invalid judgment output, and Genie completion/cancellation around creation, connection and streaming. These mocks validate code behavior, not real project access, quota maxima, model quality or operational image/navigation gates. Real successful calls and measured limits remain required before closing issue #3.
