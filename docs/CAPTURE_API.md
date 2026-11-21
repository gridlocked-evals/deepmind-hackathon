# Bounded camera evidence

`backend/capture.py` implements issue #4's capture foundation. A capture is immutable input for later model jobs. Retrying inference reads the same retained bytes; it never calls a camera again. Live-provider acceptance and the UI history selector remain integration work.

## Record a registered video source

```sh
curl -X POST http://127.0.0.1:8000/api/captures/sources/511ny-32998/start
curl http://127.0.0.1:8000/api/captures/sources/511ny-32998/history
```

The asynchronous recorder fetches the selected source's actual registered HLS URL. It retains a bounded 180-second ring of complete MPEG-TS segments. History reports its `recordingId`, `earliestSeconds`, `latestSeconds`, and selectable `segmentBoundaries`. Initially the bounds are null. Times are relative to this recording, not wall-clock exposure times. No arbitrary “30 seconds ago” history is fabricated.

Use the returned recording ID and **existing boundaries** when sealing an episode:

```json
{
  "sourceId": "511ny-32998",
  "sceneId": "511ny-32998",
  "recordingId": "rec_REPLACE_WITH_RETURNED_ID",
  "startSeconds": 0,
  "endSeconds": 30,
  "splitSeconds": 20
}
```

Send this body to `POST /api/captures/episodes`. The exact total original duration must be greater than zero and at most 120 seconds, including any held-out suffix. `splitSeconds` is optional; when present it must be an interior segment boundary. This implementation deliberately rejects non-boundary selections instead of silently trimming or returning fewer seconds than requested. A mismatch between source and scene is rejected.

Both the sum of declared segment durations and the sum of decoded/audited durations must fit the 120-second cap. Small per-segment decoding tolerance never relaxes that episode limit. `durationSeconds` and selection boundaries use the HLS-declared timeline (`timelineBasis: hls_declared_segment_duration`); `auditedDurationSeconds` and `auditedPrefixDurationSeconds` separately retain measured media durations. Segment duration drift is not corrected into an invented exposure clock. Prefix/suffix isolation uses whole, physically separate segment files, so timing drift cannot cause the suffix file to enter a prefix read. Downstream extraction must retain this declared-versus-decoded timing uncertainty when assigning evidence timestamps.

`POST /api/captures/sources/{source_id}/stop` cancels recording without deleting sealed episodes. Upstream failures stop the recording and report `unavailable`. Starting an unavailable recording clears only its ring and gives it a fresh recording ID. Accepted episode bytes remain intact. Recording does not automatically resume after a server restart; retained history and episode manifests are loaded from disk.

## Capture a snapshot

`POST /api/captures/snapshots` accepts `{"sourceId":"<id from /api/cameras>"}` and optional matching `sceneId`. The server resolves the source through the DOT registry and fetches only the official camera-ID image endpoint. Arbitrary URLs are never accepted.

Snapshots have zero temporal duration, an allowed interval of `[0,0]`, unknown exposure time, and `motionClaimsAllowed: false`. Consecutive byte-identical images are labeled `cadence.repeated`; actual unique-frame cadence remains unknown. A repeated image does not establish whether the camera is frozen or the street is unchanged. Snapshot capture does not create trajectories, flow estimates, or historical video.

## Immutable provenance and isolation

`GET /api/captures/episodes/{capture_id}` returns the manifest for evidence review. It contains source identity/location, received time, source clock semantics, all per-file SHA-256 hashes, byte lengths, audited video frame counts and durations, and partition membership. HLS program date/time is retained when supplied, but is not asserted to be exposure time. Clock uncertainty remains explicitly unknown. A successful poll with no new media eventually becomes `stale` based on the last new segment's receipt time.

`originalMediaSha256` hashes the exact original file bytes concatenated in source order. Per-file hashes and lengths preserve the boundaries. `manifestSha256` separately hashes the canonical JSON manifest excluding `manifestSha256` itself, binding those bytes to their source and timing metadata. Manifests and media are checked again when read.

Prefix and suffix segments are copied into **different files and directories** when an episode is sealed. `GET /api/captures/episodes/{capture_id}/media/{media_index}` and `CaptureService.read_prefix()` serve only prefix media; suffix requests return 403. The full manifest is for capture/evaluation review and must not be passed to inference. `CaptureService.capture_context(capture_id)` returns the validated shared `CaptureContext` with UTC `Z` timestamps and the permitted prefix interval; it excludes suffix frame metadata, file paths, and counts. Later model stages must use this context and prefix-only reads, and must not accept arbitrary episode-directory paths.

## Limits and retention

- `CAPTURE_STORAGE_DIR` selects the local data directory; `CAPTURE_MAX_BYTES` caps disk use. Configure both on the backend. Do not commit media or manifests.
- Every accepted episode is pinned as `capture-retention`. Jobs can add durable pins with `pin(capture_id, job_id)` and remove only their own reference using `unpin`. Retention management may explicitly remove `capture-retention` when an episode is no longer required.
- Storage pressure may delete only unpinned episodes. If pins fill the budget, new work returns 507 and existing evidence remains readable. There is no public deletion/unpin route in this foundation.
- Segment responses are capped at 16 MiB and playlists at 1 MiB. Incomplete bodies, partial responses, unsupported encodings, frame/decode failures, and mismatched declared duration fail explicitly.
- `ffprobe` is required for both image decode and video duration/frame audits. Audit subprocesses have timeouts and are killed on cancellation. `ffmpeg` is used only by the optional real-media unit test.
- Encrypted HLS, initialization maps/fMP4, byte ranges, discontinuities, and missing media-sequence intervals are rejected. Extend and test these formats before accepting them. No four/eight-second or fixed frame-count truncation is applied.
- Camera selection and public model-job authorization are separate concerns. These local routes currently expose capture controls for the local application; add deployment authentication before exposing write routes publicly.

## Validation

`python -m pytest backend/test_capture.py -q` covers video/snapshot semantics, exact byte hashes, 120-second caps, source mismatch, unavailable history, immutable retries/restarts, durable pins, ring eviction, failed storage reservations, held-out access isolation, repeated snapshots, source clocks, sequence-gap recovery, and ffprobe rejecting malformed or misdeclared media. HTTP fixtures are synthetic. No real camera recording result is claimed by these tests.
