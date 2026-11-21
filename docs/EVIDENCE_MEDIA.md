# Immutable model evidence preparation

`backend/evidence_media.py` prepares the sealed camera prefix for Hydrogen observation and bounded reinspection. It does not call models, fetch cameras, or read held-out suffix files.

```python
from backend.evidence_media import prepare_evidence
from backend.contracts import ImageRegion, TimeInterval

prepared = await prepare_evidence(
    captures, capture_id,
    window=TimeInterval(startSeconds=2.0, endSeconds=8.0),
    crop=ImageRegion(x=0.25, y=0.25, width=0.5, height=0.5),
    max_frames=32,
)
```

Omit `window` to use the whole permitted prefix and omit `crop` for the whole image. A requested video window is half-open and must lie inside the capture's permitted interval. Snapshots accept only `[0,0]`. `PreparedEvidence.capture` retains immutable capture identity/hash/timing metadata while narrowing `allowedInterval` to the requested subset. Callers must verify that subset against the persisted capture; they must never widen it from a model response.

## Input and output boundaries

The only byte source is `CaptureService.read_prefix(capture_id, media_index)`. Source hashes and byte counts are verified again; suffix entries are excluded before any decoder, frame-count calculation, or metadata payload. A temporary job pin protects the capture during preparation, including cancellation. Input files in the temporary decoder directory are exact copies of the retained bytes. The temporary directory is cleaned on success or failure.

`PreparedEvidence.frames` is an ordered tuple of `EvidenceFrame` values. Each contains immutable `data` bytes, `mime_type`, actual `time_seconds`, original decoded `frame_index`, `source_media_index`, original media hash, image hash, dimensions, actual source `region`, and `source_pts_seconds`. Full snapshots retain their exact original PNG/JPEG bytes. Crops and video frames are deterministically encoded as JPEG without scaling or generated visual content. Their image hashes are distinct from the original-media hash.

`prepared.metadata()` contains the capture context, frame labels and hashes, actual timestamps, crop mappings, and sampling audit. It contains no image bytes or suffix facts. Pair each metadata entry with its corresponding raw image when constructing a model request. `frame.to_evidence_ref(relative_region)` validates a normalized ROI in that supplied image and maps it back to the original source image. Passing no ROI references the whole supplied image. Pixel-rounded crop bounds are recorded, so a requested fractional crop never hides rounding from downstream evidence checks.

## Timing and sampling

ffprobe decodes every permitted segment and enumerates actual frame presentation timestamps. The frame count must match the immutable capture audit. Missing, duplicate, non-monotonic timestamps, changing dimensions within a segment, or frame times that overlap a declared segment boundary are rejected rather than replaced with an assumed FPS.

A frame timestamp is calculated as:

```text
capture-relative time = declared segment start + decoded frame PTS - first decoded segment PTS
```

`source_pts_seconds` preserves the measured PTS, including nonzero MPEG-TS clock offsets. `frame_index` is the decoded ordinal across the original prefix's segments, starting at zero. This is an index within the captured media, not an absolute camera lifetime index. Segment clocks reset independently; source exposure time and HLS duration drift remain explicitly uncertain. No `fps` filter generates or invents output timestamps.

The sampler normally selects at most one frame per second and always includes the last eligible frame. The final gap may therefore be shorter than one second. If the frame budget is lower, the sampler increases spacing across the entire permitted window and still retains both ends; it never silently drops the tail. Video requires a frame budget of 2–121; snapshots permit 1–121 and return one image. The default is 121 frames, and callers can request fewer.

Sampling metadata records decoded/eligible/selected/omitted counts, every selected timestamp, the applied minimum spacing, largest actual gap, first/last selected times, and limitations. Sparse ordering does not establish trajectories, flow rates, actor identity across occlusion, or coverage of events between samples. A snapshot provides no temporal motion evidence.

## Explicit resource limits

- The shared capture context still caps original source duration at 120 seconds. Decoded prefix duration is independently checked against that limit.
- Permitted source bytes are capped at 512 MiB, ffprobe output at 32 MiB, and decoded frames at 100,000. Exceeding an audit budget fails the request.
- Model image payloads default to 10 MiB including base64 expansion and evidence metadata, leaving room below the gateway's 12 MiB JSON limit for instructions. Callers remain responsible for the final complete request size.
- JPEG output is streamed through a bounded pipe. Decoder output that exceeds the remaining budget stops the subprocess and returns an error; it cannot fill an unbounded output directory.
- Every selected output image must be present. Image framing, exact output count, and payload bytes are checked before returning success.
- Preparation has a 90-second deadline; individual decoders have 30-second deadlines and are killed on cancellation. Missing media tools return 503, byte-budget excess 413, invalid bounds or ambiguous timing 422, decode/audit failure 502, and timeout 504.

No partial preparation is returned as success. A caller may explicitly request a smaller window, lower frame budget, or crop after a budget rejection; that new selection remains visible in its metadata.

## Validation

Run `python -m pytest backend/test_evidence_media.py -q`. Tests use actual ffmpeg-generated media and ffprobe decoding to verify nonzero PTS offsets, precise original frame indices, multiple segment clocks, crops (including odd pixel dimensions), whole-window bounded sampling, unchanged snapshots, immutable-byte corruption rejection, held-out suffix isolation, explicit payload/audit failures, JPEG framing, and pin cleanup on cancellation. Network access is forbidden by the fixture transport. These tests do not claim a successful model inference.
