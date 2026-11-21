# Camera API and playback

The camera service supplies a fixed 511NY live-video catalog, a cached NYC DOT snapshot directory, and an HLS proxy. It does not capture footage, run inference, or derive the demo simulation from video. No model credentials are required.

Run `npm run dev:api` after installing `backend/requirements.txt` into `.venv`. Vite proxies `/api` to `http://127.0.0.1:8000`. If `dist/` exists when the backend starts, FastAPI also serves that build. Source code: [backend](../backend/main.py), [HLS registry](../backend/feeds.py), [proxy](../backend/proxy.py), and [browser player](../src/media/LiveFeed.tsx).

## Routes

All application routes below use `GET`.

| Route | Response |
| --- | --- |
| `/api/health` | API readiness: `status`, `service`, and UTC `timestamp`. Does not probe camera availability. |
| `/api/vision/cameras` | `{ cameras, metadata }` for five configured 511NY HLS sources. The retained `vision` path does not imply inference. |
| `/api/cameras` | `{ cameras, metadata }` for validated NYC DOT snapshots from all five boroughs. Directory failure is represented in metadata, with HTTP 200. |
| `/api/feeds/{camera_id}/index.m3u8` | Rewritten HLS master playlist for an allowlisted camera. |
| `/api/feeds/{camera_id}/resource/{token}` | Rewritten child playlist or streamed media identified by a server-issued token. Supports a single valid byte range. |

Examples:

```sh
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/vision/cameras
curl http://127.0.0.1:8000/api/feeds/511ny-32998/index.m3u8
```

Follow resource URLs returned by a current playlist. Do not construct tokens or persist them as permanent media links.

## Camera records and status

Both directories use the frontend [CameraSource](../src/types.ts) shape. Coordinates are always `[longitude, latitude]` at the actual camera location.

```json
{
  "id": "511ny-32998",
  "providerCameraId": "32998",
  "name": "West Street at Vestry Street",
  "kind": "hls",
  "coordinates": [-74.01148453531307, 40.722891497915406],
  "provider": "511NY / NYSDOT",
  "sourceUrl": "https://511ny.org/camera/32998",
  "playbackUrl": "/api/feeds/511ny-32998/index.m3u8",
  "resolution": "640 × 352",
  "capturedAt": null,
  "fetchedAt": null,
  "availability": "unverified",
  "description": "Public roadway video · source clock and broadcast delay unknown"
}
```

`kind` is `hls` or `snapshot`. `sourceUrl` links to the official viewer; `playbackUrl` supplies the actual media. Snapshot records also include `thumbnailUrl`. `borough`, `resolution`, `description`, `thumbnailUrl`, and `fetchedAt` are optional in the shared type; `providerCameraId` is an additional HLS API field.

`capturedAt` is currently always `null`: neither directory establishes a source capture clock. HLS `fetchedAt` records the last successful proxy media fetch; snapshot `fetchedAt` records directory retrieval. Neither is the time pictured in the footage. The browser's “Image loaded … ET” label records a successful image load and uses `America/New_York`.

| HLS `availability` | Meaning |
| --- | --- |
| `unverified` | No recorded proxy success or failure in this process. |
| `connected` | The most recent recorded proxy operation succeeded, within 60 seconds. A success is recorded after a media response finishes streaming. |
| `stale` | The most recent operation succeeded more than 60 seconds ago. |
| `unavailable` | The most recent recorded proxy operation failed. The previous successful `fetchedAt` is retained. |

These are transport observations, not proof that the browser is displaying advancing video. Manifest retrieval alone does not record success. Direct browser playback bypasses proxy status accounting. Process restarts clear this history.

The live directory's `metadata.mode` is `configured`, with `registryUrl`, `captureClock: "unknown"`, and a source notice. The service uses the fixed catalog; it does not fetch the full 511NY registry on each request.

The DOT directory has a one-hour in-memory cache and a serialized refresh. Metadata includes `mode: connected | stale | unavailable`, `fetchedAt`, and `cached`. Failed refreshes return the prior records as `stale`, preserving their original successful timestamps. Without prior data, the response contains an empty list and `mode: unavailable`. Provider-reported online snapshots start as `unverified`; offline entries start as `unavailable`. Images themselves load directly from NYC DOT in the browser.

DOT records include Manhattan, Brooklyn, Queens, Bronx, and Staten Island, including cameras reported offline. Each record carries the provider’s `borough`. A broad NYC envelope (latitude `40.45–40.95`, longitude `−74.30–−73.65`) rejects invalid coordinates without clipping outer-borough cameras; duplicate and invalid IDs are discarded. Metadata includes `coverage`, `total`, and `boroughCounts`. These counts describe the public DOT directory, not every privately operated camera in New York. Image URLs must use HTTPS on `webcams.nyctmc.org`; otherwise the standard image URL is constructed from the validated camera ID. The fetched directory is capped at 8 MiB.

## HLS proxy behavior and limits

The proxy accepts camera IDs and opaque resource tokens, never a client-supplied upstream URL. Each camera permits HTTPS on `s9.nysdot.skyvdn.com`, port 443/default, within its exact `/rtplive/R11_###/` subtree. Credentials, fragments, control characters, backslashes, traversal segments, and unsupported encoded paths are rejected. Each redirected destination is validated before fetching; at most two redirects are followed.

Playlists must be UTF-8 HLS beginning with `#EXTM3U`. Relative and absolute media lines, plus quoted `URI="…"` attributes such as keys, maps, and alternate playlists, are validated and rewritten to local token routes. Query strings are retained. Unquoted URI attributes and out-of-allowlist locations are rejected.

| Limit | Value |
| --- | --- |
| Concurrent proxy requests per process | 8; wait up to 1 second for a slot |
| HTTP connection / pool timeout | 5 seconds / 3 seconds |
| HTTP read and write timeout | 10 seconds per operation, not a total stream deadline |
| HTTP connection pool | 12 connections, 8 keep-alive connections |
| Playlist response | 1 MiB maximum |
| Media response | 16 MiB maximum |
| Resource references | 4,096 in-memory tokens; least-recently-used eviction |
| Token lifetime | 900 seconds from the last issuance, playlist rewrite, or successful resolution |

Resolving a token updates its eviction order and slides its expiration to 900 seconds after that use, so an actively polled playlist stays valid. Tokens are camera-specific and disappear on restart. Use one backend worker for this implementation: tokens and status are process-local, so requests routed across independent workers can receive unknown-token errors.

The proxy forwards only the validated `Range` request header and requests identity encoding. It accepts single ranges such as `bytes=0-4095`, `bytes=4096-`, or `bytes=-4096`; explicit ranges and suffix lengths cannot exceed 16 MiB. Media preserves upstream 200/206 status and available `Content-Range`, `Accept-Ranges`, and `Content-Length` headers. Upstream nonidentity media encoding is rejected. Allowed media types are MPEG-TS, MP4 video/audio, AAC, and octet-stream; playlists receive `application/vnd.apple.mpegurl`.

Playlists use `Cache-Control: no-store`; media uses `private, no-store`. Streams close upstream responses and release their concurrency slots on completion or generator cleanup. A disconnect detected during streaming stops delivery. There is no recording or media cache.

Errors before streaming begins use FastAPI JSON, for example `{"detail":"Unknown camera source."}`:

| Status | Condition |
| --- | --- |
| 404 | Unknown camera; unknown, expired, evicted, or wrong-camera token. |
| 416 | Invalid, multiple, reversed, or oversized explicit byte range. |
| 502 | Upstream failure/timeout, unsupported location/content, too many redirects, invalid playlist, or response size violation detected before streaming. |
| 503 | No proxy slot became available within one second. |

If an unknown-length media stream exceeds the byte cap or fails after headers have been sent, the stream terminates; it cannot change to a JSON 502 response at that point. Upstream 4xx/5xx responses are mapped to 502 rather than forwarded. Errors do not return upstream response bodies or credentials.

Development CORS permits `http://localhost:5173` and `http://127.0.0.1:5173`, GET, and the Range header. Same-origin production requests do not require CORS. Source attribution and access terms are recorded in [third-party notices](../THIRD_PARTY_NOTICES.md).

## Browser lifecycle

[useCameraSources](../src/media/cameras.ts) starts with the five-source catalog, fetches both directories in parallel, and merges valid records by ID. Requests time out after 12 seconds. Refreshes occur every 60 seconds while visible and when the page becomes visible, without overlapping refresh cycles. The configured HLS catalog remains selectable when the API is unreachable.

The hook’s `loading | connected | stale | offline` status describes directory access, not stream health. Both directories failing or reporting unavailable yields `offline`. Either directory failing, reporting unavailable, or returning stale data yields `stale`; `connected` requires both directories to be available and current. On refresh failure, previously loaded cameras remain browsable with stale availability. A successful directory replaces its own prior records, allowing removed cameras to disappear after a healthy refresh.

`LiveFeed` mounts one selected source. It uses native HLS when supported, otherwise lazy-loads hls.js. Playback is muted and inline. It tries the same-origin proxy first, then the known direct provider URL once; direct playback depends on provider CORS. A 12-second connection/buffering watchdog advances this fallback. hls.js allows one media-error recovery per source attempt; manifest retries are disabled, while level and fragment loading each allow one retry.

Player states are `connecting`, `playing`, `buffering`, `paused`, `unavailable`, and `image`. Only the browser's playing event produces “LIVE SOURCE.” Autoplay rejection exposes a Play button; exhausted failures expose Retry and retain the official source link. Hidden pages pause video and stop hls.js loading; eligible playback resumes on return. Source changes, retries, and unmount destroy the old decoder and clear its media URL. The app unmounts the player when feeds are disabled.

Snapshots refresh every five seconds while visible, skip refresh while a request is pending, and use a 12-second load watchdog. Successful image loads update the displayed retrieval time; failures do not advance it. Snapshot labels remain “STREET IMAGE.”

## Adding a source

1. Verify the official camera ID, actual coordinates, current HLS master/child playlists, and media response. Keep source attribution and applicable access terms with the entry.
2. Add the source to `_ROWS` in [backend/feeds.py](../backend/feeds.py). Its tuple order is **ID, street, latitude, longitude, stream path, resolution**. The current proxy supports only the existing provider hostname and per-camera path convention; a different provider requires an explicit adapter and validation tests.
3. Add the matching row to `SOURCES` in [src/media/cameras.ts](../src/media/cameras.ts). Its tuple order is **ID, street, longitude, latitude, stream path, resolution**. This generates both offline catalog and direct-fallback entries. Keep availability initially `unverified` and capture time `null`.
4. Extend [backend tests](../backend/test_cameras.py) for identity/coordinates and any new playlist behavior. Run `npm run test:api` and `npm run build`, then verify actual browser playback, source switching, hidden-tab behavior, and feeds off. Confirm old decoder requests stop.

DOT snapshots are discovered through the directory adapter, so ordinary new NYC DOT entries require no manual catalog change. Adding a camera does not create or calibrate a simulation for that location.
