# Gotham Forge

A geographic simulation workspace for New York: a citywide public camera directory, selectable street starting points, driving-route playback, an illustrative Vestry Street traffic scene, and independently advancing public camera footage.

## Start locally

Requires Node 22.12+ and Python 3.11+. No model keys, GPU runtime, SUMO, or capture service is required.

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
- **City** shows the public camera network across all five boroughs. Select a camera in the list or on the map. Search street names or boroughs, filter by borough and source type, and page through the full directory. Click a numbered map cluster to zoom into its cameras.
- Zoom into a street and click it to open location actions. **Start here** previews up to 250 metres of available basemap street geometry. Local preview direction is unverified; a camera or point without nearby street geometry opens a stationary location preview.
- **Plan a route** accepts a starting point and destination from camera search or **Pick on map**. Choose **Find driving route**, inspect distance and directions, then **Drive this route**. Routes stop at the destination; pressing Play again replays the journey.
- **Intersection** frames the current simulation. **Follow** tracks the white ego SUV. Dragging releases follow mode. The initial simulation remains the authored Vestry Street scene.
- **Cameras** toggles all video surfaces. The selected source uses one video element; other cameras remain registry entries. Pin the City feed into the dock or collapse it. Hiding or collapsing the feed releases its playback resources.
- The bottom timeline controls the current simulation only: play/pause, reset, seek, and 0.5×/1×/2× speed. Live footage keeps its own clock.
- Click a simulated vehicle to inspect it. **Layers** controls traffic actors, the route, and camera overlays.
- Keyboard shortcuts: Space for simulation playback, C for cameras, 1/2/3 for views, / for camera search, Escape to close popovers.

Changing cameras preserves the active simulation and its replay time. Intersection/Follow return to that simulation. Starting a new local preview or driving a planned route resets playback to its new starting point. Camera footage keeps an independent clock throughout.

## What is real

Map context is OpenFreeMap/OpenStreetMap geographic data. The five configured video sources are 511NY/NYSDOT cameras, and the DOT directory supplies periodic snapshots across Manhattan, Brooklyn, Queens, Bronx, and Staten Island. This covers the public DOT directory, including entries reported offline; it does not enumerate private cameras. A source is labeled live only while the player is advancing. Source capture time and broadcast delay are unknown. Snapshot timestamps indicate retrieval, not exposure time.

The Vestry demo is **hand-authored illustrative traffic**, with a fixed geographic origin and deterministic seed. Lane details, signals, and vehicle motions are not reconstructed from camera footage. It has 24 surrounding vehicles and one ego SUV over a 60-second loop. No real-world safety or congestion measurements are claimed.

Driving routes come from the OSRM public routing service over OpenStreetMap’s driving network. The route preview uses metric street geometry, bounded acceleration and braking, corner slowdown, and a final stop. Its assumed 25 mph cruise cap is not a surveyed speed limit. Routing estimates and simulated duration differ; neither includes measured live traffic. Lane assignments, signal phases, traffic actors, road elevations, and camera-derived observations are not inferred for these routes. A basemap-only local preview is not a verified legal driving route.

Routing sends the two selected coordinates directly from the browser to `router.project-osrm.org`. Requests are user-triggered, time out after 15 seconds, and are cancelled when endpoints change. Routes longer than 80 km or with endpoints more than 200 metres from a routable street are rejected. A failed request shows a retryable error and never substitutes a straight line. The public demo service is best-effort; configure a suitable hosted or self-managed routing service for sustained production traffic.

OpenFreeMap, Google Fonts, DOT, 511NY, and OSRM require internet access. System fonts remain usable if font loading fails. A local dark map style preserves the authored scene if city context is unavailable. Camera failures show Retry and preserve the rest of the workspace. Failed directory refreshes retain previously loaded cameras and label directory status as partial or offline. The five configured HLS sources remain available for selection on initial offline startup; loading the citywide DOT directory requires the API and upstream access.

## Architecture

See the detailed [architecture guide](docs/ARCHITECTURE.md), [camera API reference](docs/CAMERA_API.md), and the verification notes below.

- React owns selection, presentation, and controls. One replay clock samples the deterministic run; the renderer reads frame and option refs without React updates for each display frame.
- MapLibre owns geographic projection and the shared WebGL context. A custom Three.js layer draws locally metric roads, instanced traffic, and the detailed ego vehicle. Local axes are east/north/up; headings are radians counterclockwise from east.
- `ScenarioProvider.loadScenario()` returns the typed `ScenarioBundle`. `DemoScenarioProvider` is the initial implementation. A later backend adapter can supply compatible scene geometry and timestamped frames without rewriting the view.
- Camera selection is separate from scene identity. Roadside video is an HTML layer above the map, not a calibrated projection onto it.
- FastAPI supplies camera metadata, a DOT cache, bounded media transport, and a server-only model gateway. HLS playlists resolve server-owned camera IDs and opaque resource tokens; arbitrary URLs are not accepted. The optional gateway starts without credentials and exposes read-only capability/budget status.

Public endpoints: `GET /api/health`, `GET /api/cameras`, `GET /api/vision/cameras`, `/api/models/status`, and `/api/feeds/{camera_id}/index.m3u8` with tokenized child resources. See the local `/docs` route for exact schemas.

## Optional model gateway

See [model gateway setup and verification](docs/MODEL_GATEWAY.md) for backend-only credentials, budgets, model adapters, and the opt-in live smoke command. On Windows, inspect status with `.\.venv\Scripts\python -m backend.gateway.smoke`; this makes no model calls. All five services passed [live capability checks](docs/MODEL_GATEWAY_VERIFICATION.md) on November 21, 2026. The gateway does not implement the full capture/edit/world pipeline.

The [Genie runtime](docs/GENIE_RUNTIME.md) adds authenticated session creation,
a browser WebSocket relay, frame recording, bounded controls and explicit cleanup.
The city UI is not connected to it yet. Production creation requires the trusted
accepted-reference handoff from #8 and a separate backend `WORLD_API_TOKEN`.
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

Frontend tests cover deterministic sampling, backwards seeking, interpolation, lane separation, bounded motion, signal stops, route-response validation, cancellation, metric route motion, and camera-directory retention. Backend tests cover all five boroughs, offline camera retention, duplicate IDs, registry identity, stale state, playlist rewriting, resource validation, redirects, ranges, and startup without model credentials.

For a production-style local preview, run `npm run build`, then start `npm run dev:api` and open `http://127.0.0.1:8000`. FastAPI serves the built application when `dist` exists at startup. Alternatively, `npm run preview` uses the same API proxy with the API running.

Earlier UI verification (before citywide navigation): production build, six scenario tests, and 19 API tests passed. Desktop (1440 × 900) and mobile (390 × 844) views were reviewed in-browser. Vestry HLS video visibly advanced through the local proxy. Camera changes, feed toggling, seeking, layer switches, and City/Intersection/Follow transitions were exercised; the active page kept at most one video element. Chrome reported approximately 120 map render events per second in Follow with one playing feed on the development machine; this is an observed local result, not a cross-device guarantee.

The model gateway foundation is implemented and live access has been verified; footage capture, the integrated generated-world workflow, SUMO, calibrated reconstruction, intervention search, and evaluation remain future integration work. The [issue tracker](https://github.com/Paradox560/deepmind-hackathon/issues/1) contains the current submission scope; `GOTHAM_FORGE_PLAN.md` still awaits the scope update in issue #2.
