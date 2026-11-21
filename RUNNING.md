# Gridlocked

A New York camera and driving workspace with three tools: **Cameras** for the public roadway network, **Route** for choosing where to start and go, and **Driver** for Gemini control of a simulated car. The original Vestry Street traffic demo remains available from the top bar.

## Start locally

Requires Node 22.12+ and Python 3.11+. Camera browsing and the authored demo work without model credentials. Video capture also requires `ffmpeg` and `ffprobe` on PATH. GPU runtimes and SUMO are not required for the Element 119 core.

```sh
npm ci
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
```

Start the API and frontend in separate terminals:

```sh
npm run dev:api
```

```sh
npm run dev
```

Open the local URL printed by Vite. It proxies `/api` to the FastAPI service on `127.0.0.1:8000`. If port 5173 is occupied, Vite prints the next available port.

## Model credentials

Copy `.env.example` to `.env` in the **deepmind-hackathon repository root** if `.env` does not already exist. Enter the keys locally:

```dotenv
GEMINI_API_KEY=your-tier-3-gemini-ai-studio-key
WORKSHOP_API_KEY=your-workshop-gemini-ai-studio-key
GEMINI_KEY_PROFILE=primary
```

Both credentials are Gemini AI Studio keys. `primary` selects `GEMINI_API_KEY`; `workshop` selects `WORKSHOP_API_KEY`. Selection is explicit: quota failures do not switch projects automatically. Restart the backend after changing configuration. The API and smoke CLI load this checkout's `.env`; exported environment variables take precedence.

Genie is optional: keep `GENIE_ENABLED=false` to prevent world-generation requests and preserve credits. The default smoke batch skips disabled Genie. Set `GENIE_ENABLED=true` deliberately when ready to test or use billable world generation; neither credential setup nor starting the app enables it.

`.env` is ignored by Git. Do not put keys into `src/`, `VITE_*` variables, request URLs, or committed files. The status endpoint reports configuration and connectivity without returning credentials. Tier 3 describes the project's access tier; actual model availability and quota still need a successful project-specific smoke test. See [Model gateway](docs/MODEL_GATEWAY.md) for limits and the opt-in live smoke command.

Runtime capture evidence stays in ignored `var/captures` (`CAPTURE_DIR`), and pipeline artifacts in `var/pipeline` (`PIPELINE_DIR`). The gateway uses ignored `.gateway/` and imports previous ledger reservations without resetting them. Existing `data/` evidence is retained locally; it is not silently relabeled as the new contract. Run one backend worker against a local evidence directory. See [Capture API](docs/CAPTURE_API.md) for bounded recording and prefix access.

## Extract observations from a capture

Model actions require a separate local operator token, independent of Gemini credentials and of whether Genie is enabled. Generate one locally and place the value in `.env` as `WORLD_API_TOKEN`:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Enter that token into the Evidence panel's operator field for the current browser session. Keep Gemini keys on the backend. Missing operator authentication blocks model actions; browsing and recording cameras still work.

Create a retained episode or snapshot using the [capture routes](docs/CAPTURE_API.md), then explicitly request extraction using its ID and camera ID:

```sh
curl -X POST http://127.0.0.1:8000/api/observations/extract \
  -H "Authorization: Bearer $WORLD_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"captureId":"cap_REPLACE_WITH_CAPTURE_ID","cameraId":"511ny-32998"}'
```

This makes budgeted Hydrogen requests using only the retained prefix. The extraction report preserves actual source-frame timestamps, image-space evidence, unknowns, every window outcome and gateway trace. Read a report at `/api/observations/extractions/{id}`; review the original frames beside its facts at `/api/observations/extractions/{id}/review` with operator authentication. Reinspection, manual annotation and failure accounting are documented in [the observation guide](docs/OBSERVATIONS.md).

Open **Evidence** to record the selected video camera or capture its current snapshot. Recording offers only actual buffered history. Extraction retries reuse the same sealed capture. Accepted observations can feed a Carbon mayor edit, then a Nano Banana reference and a Decisions gate. These actions are explicit and budgeted. Generated references stay labeled as generated; they do not replace the illustrative map scene. A generated world requires both an accepted reference gate and explicit backend `GENIE_ENABLED=true`. Keeping Genie off leaves capture, observations, editing and reference review available.

## Explore

- **Traffic demo** in the top bar (shown as **Demo** on smaller screens) restores the original Vestry simulation: 24 traffic vehicles plus the ego SUV, authored signals, and a 60-second loop. It resets playback to 1×, makes the traffic layers visible, and frames the intersection. Click it again to restart, or choose Follow to ride with the ego vehicle. Your planned route remains available in the route planner. Citywide zoom hides small vehicles; the routed and local previews use a single ego vehicle.
- **Cameras** in the side rail shows the public camera directory across all five boroughs. Select a camera in the list or on the map. Search street names or boroughs, filter by borough and source type, and page through the full directory. **City** frames the network; click a numbered map cluster to zoom into its cameras.
- Zoom into a street and click it to open location actions. **Start here** previews up to 250 metres of available basemap street geometry. Local preview direction is unverified; a camera or point without nearby street geometry opens a stationary location preview.
- **Route** accepts a starting point and destination from camera search or **Pick on map**. Choose **Find driving route**, inspect distance and directions, then **Drive this route** to load it into the simulation. Routes stop at the destination; pressing Play again replays the journey. Open **Driver** to let Gemini drive the loaded route.
- **Driver** shows gateway availability, the ego vehicle's speed and remaining distance, and model decisions. **Start Gemini drive** starts a new bounded run on the active route or traffic demo. It requires the optional backend credential described below.
- **Intersection** frames the current simulation. **Follow** tracks the white ego SUV. Dragging releases follow mode. The initial simulation remains the authored Vestry Street scene.
- **Cameras** toggles all video surfaces. The selected source uses one video element; other cameras remain registry entries. Pin the City feed into the dock or collapse it. Hiding or collapsing the feed releases its playback resources.
- The bottom timeline controls replay: play/pause, reset, seek, and 0.5×/1×/2× speed. During Gemini driving, it instead offers **Stop Gemini drive** and **Return to replay**; seeking and playback-speed changes are disabled. Camera footage keeps its own clock.
- Click a simulated vehicle to inspect it. **Layers** controls traffic actors, the route, and camera overlays.
- Keyboard shortcuts: Space for simulation playback, C for cameras, 1/2/3 for views, / for camera search, Escape to close popovers.

Changing cameras preserves the scene and replay time but stops an active Gemini run. Intersection/Follow return to that scene. Starting a new local preview, loading a route, or choosing Traffic demo resets playback to its new starting point. Camera footage keeps an independent clock throughout.

## Gemini driving

Set `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the backend environment or the repository's ignored `.env` file, then restart `npm run dev:api`. Existing backend configuration is reused; do not create a `VITE_*` credential. The Driver panel checks `/api/models/status` without making a model call. **Check connection** refreshes that status. A configured credential does not guarantee model access, quota, or remaining budget.

1. Choose **Traffic demo**, or use **Route** → **Find driving route** → **Drive this route**. A stationary location preview has no route to drive.
2. Open **Driver** and select **Start Gemini drive**. It starts from the active scenario's initial position and switches to Follow.
3. Watch the model's action, target speed, explanation, decision count, and history. After the first decision, simulation keeps moving while the next decision is requested, normally once per second.
4. Select **Stop drive** to end the run, **Restart Gemini drive** for a fresh run, or **Return to replay** to reset the timeline and use ordinary playback.

Each run permits at most **60 actual model decisions and 60 simulated seconds**, ending earlier if the destination is reached. The existing gateway may make bounded provider retries within one decision; its configured spending limits remain unchanged. No synthetic decision replaces a missing, failed, or invalid response. Errors stop model driving visibly. A command expires three seconds after its observation was captured; a slow or stalled request stops the run instead of applying stale instructions indefinitely.

Gemini Decisions (`gemini-decision-1`) receives structured simulated state and selects `accelerate`, `cruise`, `brake`, or `stop` from four bounded target-speed candidates. The backend maps the selected candidate to a speed and a fixed action summary; it does not generate a free-text explanation. That selected target controls the ego car; steering follows the selected route deterministically. Local physics imposes acceleration/braking limits, corner slowdown, route-end stops, a 25 mph cap, same-lane lead spacing, and the authored Vestry signal's stop line. Emergency boundaries can override comfortable braking. These constraints do not make the scene a validated traffic-safety model.

Outside Vestry, route signals are reported as **unknown**; no live signal phases or camera-derived traffic are invented. Vestry's surrounding vehicles remain authored replay and do not react to the Gemini-controlled ego, so collisions with cross traffic or vehicles approaching from behind are not comprehensively prevented. This is structured-state simulator control, separate from [#10's visual Genie navigation agent](https://github.com/gridlocked-evals/deepmind-hackathon/issues/10).

Stopping, hiding the browser tab, switching cameras, changing route endpoints, loading another scene, or resetting cancels the active run and discards late results. No further decisions are requested; an already-dispatched provider call may still finish and retain its gateway usage charge. Runs do not resume automatically. See the [Driver API](docs/DRIVER_API.md) for schemas, idempotency, server-enforced limits, errors, and synthetic-input provenance. Use one local API worker; the run registry is process-local.

## What is real

Map context is OpenFreeMap/OpenStreetMap geographic data. The five configured video sources are 511NY/NYSDOT cameras, and the DOT directory supplies periodic snapshots across Manhattan, Brooklyn, Queens, Bronx, and Staten Island. This covers the public DOT directory, including entries reported offline; it does not enumerate private cameras. A source is labeled live only while the player is advancing. Source capture time and broadcast delay are unknown. Snapshot timestamps indicate retrieval, not exposure time.

The Vestry demo is **hand-authored illustrative traffic**, with a fixed geographic origin and deterministic seed. Lane details, signals, and background vehicle motions are not reconstructed from camera footage. Ordinary replay has 24 surrounding vehicles and one ego SUV over a 60-second loop. Gemini driving replaces only the ego's longitudinal control; surrounding cars continue the authored replay. No real-world safety or congestion measurements are claimed.

Driving routes come from the OSRM public routing service over OpenStreetMap’s driving network. The route preview uses metric street geometry, bounded acceleration and braking, corner slowdown, and a final stop. Its assumed 25 mph cruise cap is not a surveyed speed limit. Routing estimates and simulated duration differ; neither includes measured live traffic. Lane assignments, signal phases, traffic actors, road elevations, and camera-derived observations are not inferred for these routes. A basemap-only local preview is not a verified legal driving route.

Routing sends the two selected coordinates directly from the browser to `router.project-osrm.org`. Requests are user-triggered, time out after 15 seconds, and are cancelled when endpoints change. Routes longer than 80 km or with endpoints more than 200 metres from a routable street are rejected. A failed request shows a retryable error and never substitutes a straight line. The public demo service is best-effort; configure a suitable hosted or self-managed routing service for sustained production traffic.

OpenFreeMap, Google Fonts, DOT, 511NY, and OSRM require internet access. System fonts remain usable if font loading fails. A local dark map style preserves the authored scene if city context is unavailable. Camera failures show Retry and preserve the rest of the workspace. Failed directory refreshes retain previously loaded cameras and label directory status as partial or offline. The five configured HLS sources remain available for selection on initial offline startup; loading the citywide DOT directory requires the API and upstream access.

## Architecture

See the [architecture guide](docs/ARCHITECTURE.md), [camera API reference](docs/CAMERA_API.md), [Driver API](docs/DRIVER_API.md), and verification notes below. The implementation summary here includes newer backend stages that earlier architecture notes may describe as planned.

- React owns selection, presentation, and controls. Replay samples the deterministic scenario; a Gemini run uses a separate bounded simulation clock. The renderer reads the active frame and option refs without React updates for each display frame.
- MapLibre owns geographic projection and the shared WebGL context. A custom Three.js layer draws locally metric roads, instanced traffic, and the detailed ego vehicle. Local axes are east/north/up; headings are radians counterclockwise from east.
- `ScenarioProvider.loadScenario()` returns the typed `ScenarioBundle`. `DemoScenarioProvider` is the initial implementation. A later backend adapter can supply compatible scene geometry and timestamped frames without rewriting the view.
- Camera selection is separate from scene identity. Roadside video is an HTML layer above the map, not a calibrated projection onto it.
- FastAPI supplies camera metadata, a DOT cache, bounded media transport, capture services, and model gateway status. HLS playlists resolve server-owned camera IDs and opaque resource tokens; arbitrary URLs are not accepted. The Evidence drawer connects camera captures to observations, Carbon edits, generated references and Decisions gates, independently from the authored demo. Optional worlds require explicit opt-in and an accepted gate.

- The simulator driver uses the gateway’s Decisions `simulator_driver` stage for structured simulated-state decisions. Its steering and physics remain deterministic; it is separate from camera perception and the visual Genie controller.

Map and driver endpoints include `GET /api/health`, `GET /api/cameras`, `GET /api/vision/cameras`, `GET /api/models/status`, `POST /api/driver/decision`, and `/api/feeds/{camera_id}/index.m3u8` with tokenized child resources. Capture, edit/reference/gate, and authenticated world routes are documented in their linked guides. See the local `/docs` route and [Driver API request contract](docs/DRIVER_API.md#request).

## Optional model gateway

See [model gateway setup and verification](docs/MODEL_GATEWAY.md) for backend-only credentials, budgets, model adapters, and the opt-in live smoke command. On Windows, inspect status with `.\.venv\Scripts\python -m backend.gateway.smoke`; this makes no model calls. All five services passed [live capability checks](docs/MODEL_GATEWAY_VERIFICATION.md) on November 21, 2026. Capture, observations, edits, references, gates and optional worlds share the same gateway and durable budget ledger.

The [Genie runtime](docs/GENIE_RUNTIME.md) adds authenticated session creation,
a browser WebSocket relay, frame recording, bounded controls and explicit cleanup.
Production creation requires the trusted accepted-reference handoff from #8,
operator authentication and explicit `GENIE_ENABLED=true`. Keep Genie disabled
when credits are unavailable; the earlier evidence and reference stages remain usable.
The opt-in development command is `.\.venv\Scripts\python -m backend.worlds.spike --live`;
it creates one bounded paid session. See its [measured verification](docs/GENIE_RUNTIME_VERIFICATION.md).

## Verify

The [Hydrogen evidence extractor](docs/OBSERVATIONS.md) implements issue #5 over
saved camera prefixes, with authenticated extraction/reinspection routes and
an optional original-pixel review export. Its explicit live checks and scope
clarification are recorded in [observation verification](docs/OBSERVATIONS_VERIFICATION.md).

```sh
npm run contracts:check
npm run build
npm test
npm run test:api
```

Frontend tests cover deterministic sampling, backwards seeking, interpolation, lane separation, bounded motion, signal stops, route-response validation, cancellation, metric route motion, camera-directory retention, and driver physics/session validation. Backend tests cover camera transport and retention, startup without model credentials, the gateway/pipeline/evaluation components, and driver schema bounds, cached retries, concurrency, cancellation, and error redaction. Driver unit tests use fake gateways and are not live-model evidence.

For a production-style local preview, run `npm run build`, then start `npm run dev:api` and open `http://127.0.0.1:8000`. FastAPI serves the built application when `dist` exists at startup. Alternatively, `npm run preview` uses the same API proxy with the API running.

Earlier UI verification (before citywide navigation): production build, six scenario tests, and 19 API tests passed. Desktop (1440 × 900) and mobile (390 × 844) views were reviewed in-browser. Vestry HLS video visibly advanced through the local proxy. Camera changes, feed toggling, seeking, layer switches, and City/Intersection/Follow transitions were exercised; the active page kept at most one video element. Chrome reported approximately 120 map render events per second in Follow with one playing feed on the development machine; this is an observed local result, not a cross-device guarantee.

The contracts, gateway, capture, and observation services are covered by automated tests. Historical live capability checks and real video/snapshot observation runs are recorded in [gateway verification](docs/MODEL_GATEWAY_VERIFICATION.md) and [observation verification](docs/OBSERVATIONS_VERIFICATION.md). Image processing is complete under the maintainer’s current scope. Human clip review is optional and does not block completion. The separate visual Hydrogen-to-Genie controller and integrated temporal judging remain #12 dependencies; the structured-state simulator driver is already implemented. SUMO and calibrated reconstruction are later milestones.
