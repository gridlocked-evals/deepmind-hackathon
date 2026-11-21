# Bounded camera capture

The merged implementation uses the canonical `CaptureStore` / `Capture` interfaces
in `backend/capture.py` and the routes in `backend/capture_api.py`. See
[CAMERA_API.md](CAMERA_API.md#capture-episodes) for the complete request shapes.
The older `/api/captures/sources/...` and `/api/captures/episodes` write routes are
replaced by the canonical routes below.

## Recording and selecting actual history

- `POST /api/captures/{camera_id}/recording` starts a registered HLS source.
- `GET /api/captures/{camera_id}/buffer` reports actual buffered history. A fresh
  recorder has none. `history.segmentBoundariesS` contains selectable whole-segment
  boundaries, measured from the oldest retained segment. `recording` distinguishes
  an active task from retained history after a stop.
- `POST /api/captures/{camera_id}/stop` stops retrieval while preserving the buffer.
- `POST /api/captures` accepts `cameraId`, `secondsAgo`, `durationS`, optional
  `holdoutS`, `sceneCameraId`, and `bufferRevision`. Send the revision returned by
  the buffer response to prevent a selection silently shifting as recording
  advances. A changed revision returns 409. Starting again after a stop or an
  unavailable source begins fresh history; sealed episodes remain intact.
- `POST /api/captures/snapshots/{camera_id}` resolves an actual DOT registry entry
  and retains one original image. Refresh the camera directory first if the ID is
  not yet registered in this process.

Offsets measure backward from the newest buffered segment, not exposure time.
Selections cover complete overlapping segments and report their exact actual
selected duration. The declared sum and the decoded original sum must both fit
120 seconds, including the held-out partition. An unavailable interval fails;
recording and inference retries never manufacture earlier history.

Source-reported program times and server receipt times remain separate. Missing
exposure time and broadcast delay stay unknown. Repeated snapshot bytes and HLS
segments are labeled. Decoded frame rate below five frames per second receives
`low_effective_cadence`; this measures decoded cadence, not a count of visually
unique frames. Stale and unavailable states do not assert the camera is current.

## Mandatory media audits

`ffprobe` is required. Every original MPEG-TS segment in both partitions must decode
successfully, with positive frame count, bounded dimensions and a duration within
max(0.25 seconds, 5%) of its playlist claim. A failed probe is rejected, never
converted to an accepted unknown. Segment duration tolerance does not relax the
120-second decoded total cap. Snapshots must decode exactly one image and have the
correct image signature. `auditedTotalDurationS`, `auditedPrefixDurationS` and
per-segment `auditedDurationS` retain decoded timing separately from playlist timing.

The ring is bounded to 180 seconds / 96 MiB per source; each segment is at most
16 MiB and each snapshot at most 2 MiB. Captures permit at most 100,000 decoded
frames and 1,000 original segments. Two capture audits may run concurrently;
additional admission returns 503. Decoder output, per-process execution time,
allocations and allowed protocols/formats are bounded. Capture audits have an
additional overall deadline. Encrypted media, fMP4, byte ranges, discontinuities
and sequence gaps are rejected. A sequence rollback makes the recorder unavailable
until an explicit fresh start.

## Immutable provenance, playback and retention

Each manifest binds original file and segment bytes to SHA-256 hashes, source
identity, receipt/source clocks, dimensions and decoded counts. New manifests add
`integrityVersion: 1` and a canonical `manifestSha256`. Legacy main-branch manifests
remain readable with structural/path and original-byte checks; they do not gain a
historical manifest hash or a claim that they passed the newer strict decoder audit.
Malformed JSON, metadata changes, invalid paths, missing media, symlinks and changed
original bytes fail closed. Original files remain read-only.

Prefix and held-out bytes occupy separate files. Model input and playback verify
and read only the prefix. Holdout access requires the existing explicit evaluation
role. Public playback routes never expose it:

- `GET /api/captures/{episode_id}/media` returns original prefix bytes.
- `GET /api/captures/{episode_id}/index.m3u8` returns a prefix-only VOD playlist with
  original segment durations; snapshots return 422.
- `GET /api/captures/{episode_id}/segments/{index}` returns one original prefix
  segment. An index beyond the prefix is denied.

Playback is `private, no-store`. Browser seeking is approximate; source evidence
frame timestamps remain authoritative.

The downstream `source_frame` helper uses the actual indexed frame at or before
the requested time and stores its decoded timestamp. Video requests use the
half-open prefix interval; snapshots accept only time zero. Each read holds a
durable temporary capture pin through verification and decoding, rechecks cached
frame provenance, and releases its pin on success or failure. It never reads the
held-out file.

`CAPTURE_DIR` selects the storage directory and `CAPTURE_BUDGET_BYTES` its budget.
Pins survive restarts. Pin publication and eviction share the main implementation's
lock, preventing successful pins from racing deletion. New episodes explicitly
record an empty pin list; missing or corrupt pin records are recovered conservatively
as retained. Damaged manifests are never automatically evicted. Storage pressure
can remove only explicitly unpinned valid episodes. Incomplete temporary episodes
are discarded at startup; recording itself does not resume automatically.
New captures count damaged retained episodes against the budget and reject before
evicting anything when the remaining reclaimable space cannot accommodate them.

## Verification

`python -m pytest backend/test_capture.py -q` covers actual bounded history, stale
and repeated sources, source/scene mismatch, immutable retries, snapshot/video
semantics, real ffmpeg/ffprobe decoding, failed-prefix/held-out audits, decoded
120-second limits, pin/eviction races and recovery, tamper/path failures, bounded
decoder output/time, revision guards, and prefix-only playback. Mocked transport
and synthetic annotations are not real-world accuracy measurements. The retained
real public-camera input runs are documented separately in
[OBSERVATIONS_VERIFICATION.md](OBSERVATIONS_VERIFICATION.md).
