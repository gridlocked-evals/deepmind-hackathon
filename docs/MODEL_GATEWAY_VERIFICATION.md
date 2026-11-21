# Live gateway verification — November 21, 2026

All five configured services returned validated outputs and reported token usage
between 12:02 and 12:05 p.m. America/New_York. These were bounded synthetic
development probes across multiple episodes, not a real-camera submission run or
a project throughput benchmark. Credentials remain in the ignored local `.env`.

## Successful checks

The table uses the latest successful recorded attempt for each service. Latency
is total gateway wall time for that call, not first-token/frame latency.

| Exact model | Verified behavior | Latency | Input / output / thought tokens |
| --- | --- | ---: | ---: |
| `gemini-4-hydrogen` | Read the fixture image, identified both colored rectangles and returned a validated count of 2. | 3.032 s | 1,346 / 74 / 134 |
| `gemini-4-carbon` | Compiled a protected-bike-lane request while preserving sidewalks and the marked crossing. | 6.981 s | 281 / 44 / 229 |
| `gemini-nano-banana-2.1` | Edited the fixture into a decodable generated image. | 15.818 s | 1,149 / 1,482 / 773 |
| `gemini-decision-1` | Returned a typed image judgment with probability and confidence. | 0.493 s | 1,200 / 0 / 0 |
| `genie-4` | Accepted the generated reference, streamed 12 JPEG frames, accepted key-down/key-up events, and acknowledged `interaction.completed`. | 9.304 s | 86 / 2,880 / 0 |

The image request used the 16:9 preset; the returned dimensions were **1376 × 768**.
Genie accepted those bytes. Exact image-dimension normalization and acceptance
checks remain part of issue #7; the preset alone does not guarantee exact 16:9
pixel arithmetic. This probe did not measure a causal visual response to the
short key press, sustained navigation, or visual rule compliance.

## Failures and corrections retained

- Two Nano Banana attempts returned HTTP 400 with the diagnostic “Image delivery
  mode is not supported.” The adapter removed `response_format.delivery` and
  successfully validated the default inline image response. Both failed attempts
  remain in the ledger; no retry-until-pass loop was added.
- Stateless Hydrogen/Carbon/image responses and the Decisions responses omitted
  provider request IDs. The first smoke reports treated those omissions as
  incomplete verification. The revised report distinguishes durable **local
  attempt IDs** from optional provider IDs, and still requires reported usage.
  It does not fabricate missing server IDs or enable server storage just to get
  one. Genie returned the interaction ID needed for termination.
- Both live Genie probes explicitly completed. The ledger contains **no unresolved
  world sessions** and no budget-overrun blocks.

The post-fix Nano Banana → Genie command exited successfully:

```powershell
.\.venv\Scripts\python -m backend.gateway.smoke --live --services nano_banana genie
```

## Evidence locations

Private local request/result artifacts and reports live in
`.gateway/artifacts/<sha256>.json`; the append-preserved evidence is indexed by
`.gateway/usage.sqlite3`. They are ignored by Git. Retain that directory with the
experiment package; these hashes alone do not carry the underlying media.

| Service | Local attempt ID | Result artifact SHA-256 |
| --- | --- | --- |
| Hydrogen | `6489d09a-a624-44dc-83ba-27b12b56ab8f` | `8777ad220bf76039d7e64ab2743f5c18ab42fd10d3007000491a1fd55b6bfb4f` |
| Carbon | `ee7700c0-1b12-4265-90c0-a1b9c3e458b0` | `05fe290264c9c43d99c0534b397562fe6850408b629a93246df25d7894e264f6` |
| Nano Banana | `667fdb7d-fcdf-4b2b-8caf-b574672120b7` | `d091aa45a99780bbc10a7b8dff41b9ebaee80ae39cfc7bef01e7f6f2e88aee21` |
| Decisions | `5e4b6e73-d255-4496-ba0c-703c1572b38e` | `0089820c258885eda5897c70267153f54029bf189b6c0e70e6c057d275371a58` |
| Genie | `ee7b34cd-a11e-456b-8f23-385ced35f784` | `e6b2e8960564690c833ada62dd3e5806cf108678257346f53b2af058e293b71e` |

Consolidated read-only verification report:
`176ec2dd576464e3e517ed96ac68a71e5bed97bb14184ff08ac9d6944bff3323`.

Original full-run report:
`cdc978cbb2438cafd29c839cb4639fd7b0b098e0467bc630da20b8bcfe27b732`.

Diagnostic-run report:
`0dd7195288c8a3106c1def1875aae44c872dadc716390523e830b1765d86d71f`.

Post-fix image/world report:
`a0cf717b7dbb8310889aa866baed00391e30d51a1e90f5277c4a27c453bd7f40`.

## Accounting and remaining work

Across all verification attempts, reported usage produces an estimated upper
bound of **$0.199031** under the configured rate card. Two rejected requests
without usage retain **$0.344064** in conservative reservations, giving a local
budget charge of **$0.543095**. These are local estimates/reservations, not a
provider invoice or a claim that rejected requests were billed. The $5 demo
reserve was not used.

Project RPM/TPM ceilings, sustainable control cadence and load behavior remain
unmeasured. Issue #3's integrated submission requirement still needs real-camera
Hydrogen observations and Carbon edits through the application pipeline. This
record establishes service access and bounded method execution, not completion
of that later acceptance gate.
