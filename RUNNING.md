# Gridlocked

A New York camera and driving workspace with three tools: **Cameras** for the public roadway network, **Route** for choosing where to start and go, and **Driver** for Gemini control of a simulated car. The original Vestry Street traffic demo remains available from the top bar.

## Start locally

Requires Node 22.12+ and Python 3.11+. Cameras, route previews, and traffic replay need no model key, GPU runtime, SUMO, or separate capture service. Gemini driving additionally uses the backend's existing model credential; see [Gemini driving](#gemini-driving).

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
3. Watch the model's action, target speed, explanation, decision count, and history. Simulation pauses while awaiting a response, then advances for up to three seconds under that decision.
4. Select **Stop drive** to end the run, **Restart Gemini drive** for a fresh run, or **Return to replay** to reset the timeline and use ordinary playback.

Each run permits at most **12 actual model decisions and 36 simulated seconds**, ending earlier if the destination is reached. The existing gateway may make bounded provider retries within one decision; its configured spending limits remain unchanged. No synthetic decision replaces a missing, failed, or invalid response. Errors stop model driving visibly.

Gemini Hydrogen receives structured simulated state and chooses `accelerate`, `cruise`, `brake`, or `stop` with a target speed. Its returned target controls the ego car; steering follows the selected route deterministically. Local physics imposes acceleration/braking limits, corner slowdown, route-end stops, a 25 mph cap, same-lane lead spacing, and the authored Vestry signal's stop line. Emergency boundaries can override comfortable braking. These constraints do not make the scene a validated traffic-safety model.

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
- FastAPI supplies camera metadata, a DOT cache, bounded media transport and capture storage, plus a server-only model gateway. HLS playlists resolve server-owned camera IDs and opaque resource tokens; arbitrary URLs are not accepted. The optional gateway starts without credentials and exposes read-only capability/budget status.
- The simulator driver calls the existing gateway's Hydrogen `navigation` stage with a structured response schema. It retains bounded per-run decisions, rejects duplicate conflicts, and returns cached results for identical retries. Credentials and usage accounting stay on the backend.
- Backend capture, edit, reference, gate, Genie-session, and evaluation components exist independently of the map UI. The camera-image observation and generated-world workflows still need their integrated UI/controller handoffs; their outputs do not silently replace simulated scene state.

Map and driver endpoints include `GET /api/health`, `GET /api/cameras`, `GET /api/vision/cameras`, `GET /api/models/status`, `POST /api/driver/decision`, and `/api/feeds/{camera_id}/index.m3u8` with tokenized child resources. Capture, edit/reference/gate, and authenticated world routes are documented in their linked guides. See the local `/docs` route and [Driver API request contract](docs/DRIVER_API.md#request).

## Optional model gateway

See [model gateway setup and verification](docs/MODEL_GATEWAY.md) for backend-only credentials, budgets, model adapters, and the opt-in live smoke command. On Windows, inspect status with `.\.venv\Scripts\python -m backend.gateway.smoke`; this makes no model calls. Earlier service capability checks are recorded in [model gateway verification](docs/MODEL_GATEWAY_VERIFICATION.md); they are separate from verification of the new simulator driver.

Capture episodes are implemented in the [camera API](docs/CAMERA_API.md#capture-episodes). Carbon edit compilation, Nano Banana reference generation, Decisions gates, bounded re-edits, immutable artifacts, and human-review calibration are implemented in the [edit/reference/gate backend](docs/EDIT_REFERENCE_GATE.md). That guide identifies the development Hydrogen anchor stand-in and the remaining full-observation integration. The [evaluation package](backend/evaluation/) includes temporal judging, breakpoints, repair comparison, adversarial attacks, and frozen evaluation manifests/holdout guards. Implemented components do not establish a completed external benchmark or integrated demonstration.

The [Genie runtime](docs/GENIE_RUNTIME.md) adds authenticated session creation,
a browser WebSocket relay, frame recording, bounded controls and explicit cleanup.
The city UI is not connected to it yet. Production creation requires the trusted
accepted-reference handoff from the gate and a separate backend `WORLD_API_TOKEN`;
check the runtime guide for the current handoff integration contract.
The opt-in development command is `.\.venv\Scripts\python -m backend.worlds.spike --live`;
it creates one bounded paid session. See its [measured verification](docs/GENIE_RUNTIME_VERIFICATION.md).

## Verify

The [Hydrogen evidence extractor](docs/OBSERVATIONS.md) implements issue #5 over
saved camera prefixes, with authenticated extraction/reinspection routes and
an original-pixel review export. Its explicit live checks and remaining human
annotation work are recorded in [observation verification](docs/OBSERVATIONS_VERIFICATION.md).

```sh
npm run build
npm test
npm run test:api
```

Frontend tests cover deterministic sampling, backwards seeking, interpolation, lane separation, bounded motion, signal stops, route-response validation, cancellation, metric route motion, camera-directory retention, and driver physics/session validation. Backend tests cover camera transport and retention, startup without model credentials, the gateway/pipeline/evaluation components, and driver schema bounds, cached retries, concurrency, cancellation, and error redaction. Driver unit tests use fake gateways and are not live-model evidence.

For a production-style local preview, run `npm run build`, then start `npm run dev:api` and open `http://127.0.0.1:8000`. FastAPI serves the built application when `dist` exists at startup. Alternatively, `npm run preview` uses the same API proxy with the API running.

Earlier UI verification (before citywide navigation): production build, six scenario tests, and 19 API tests passed. Desktop (1440 × 900) and mobile (390 × 844) views were reviewed in-browser. Vestry HLS video visibly advanced through the local proxy. Camera changes, feed toggling, seeking, layer switches, and City/Intersection/Follow transitions were exercised; the active page kept at most one video element. Chrome reported approximately 120 map render events per second in Follow with one playing feed on the development machine; this is an observed local result, not a cross-device guarantee.

Remaining work includes connecting camera-derived observations and the generated-world UI, completing visual Genie navigation, and validating the integrated external-camera evaluation. SUMO execution and calibrated scene reconstruction are not part of this map simulation. The [issue tracker](https://github.com/gridlocked-evals/deepmind-hackathon/issues) tracks current ownership and acceptance; `GOTHAM_FORGE_PLAN.md` and the research notes preserve earlier design directions.
