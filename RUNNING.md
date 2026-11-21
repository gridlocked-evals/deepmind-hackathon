# Gotham Forge

A geographic simulation workspace for New York: a 3D city map, an illustrative Vestry Street traffic scene, and independently advancing public camera footage.

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

Runtime media and request evidence default to the ignored `data/` directory. Run one backend worker against a local data directory; this foundation does not coordinate budgets or capture state across multiple workers. Capture API behavior, supported media, and sealed-prefix access are documented in [Capture API](docs/CAPTURE_API.md).

## Extract observations from a capture

Create a retained episode or snapshot using the [capture routes](docs/CAPTURE_API.md), then pass its returned capture ID to the observation API:

```sh
curl -X POST http://127.0.0.1:8000/api/observations \
  -H 'Content-Type: application/json' \
  -d '{"captureId":"cap_REPLACE_WITH_CAPTURE_ID","maxFrames":16}'
```

This makes a budgeted Hydrogen request using only the retained permitted media. Accepted batches contain actual source-frame timestamps, image-space evidence regions, unknowns, and the gateway trace. The response header `X-Observation-Attempt-Id` identifies a retained attempt; failures also return an attempt ID. Retrieve accepted batches at `/api/observations/{id}` and frame audits at `/api/observations/{id}/evidence`. Window/crop reinspection, temporal event ownership, error handling, and annotation limitations are in [the observation API guide](docs/OBSERVATIONS_API.md).

`OBSERVATION_STORAGE_DIR` defaults to `data/observations`. Keep that directory and its source captures together so persisted evidence links remain valid. Model extraction is currently an API workflow; the authored map demo does not yet display these observations.

## Explore

- **City** shows the geographic camera network. Select a camera in the list or on the map; search supports street names.
- **Intersection** opens the authored Vestry Street scene. **Follow** tracks the white ego SUV. Dragging releases follow mode.
- **Cameras** toggles all video surfaces. The selected source uses one video element; other cameras remain registry entries. Pin the City feed into the dock or collapse it. Hiding or collapsing the feed releases its playback resources.
- The bottom timeline controls the demo only: play/pause, reset, seek, and 0.5×/1×/2× speed. Live footage keeps its own clock.
- Click a simulated vehicle to inspect it. **Layers** controls traffic actors, the route, and camera overlays.
- Keyboard shortcuts: Space for demo playback, C for cameras, 1/2/3 for views, / for camera search, Escape to close popovers.

Changing cameras preserves the scene and its replay time. Returning to Intersection/Follow explicitly restores the Vestry scene and its associated source.

## What is real

Map context is OpenFreeMap/OpenStreetMap geographic data. The five configured video sources are 511NY/NYSDOT cameras, and the DOT directory supplies periodic snapshots. A source is labeled live only while the player is advancing. Source capture time and broadcast delay are unknown. Snapshot timestamps indicate retrieval, not exposure time.

The Vestry demo is **hand-authored illustrative traffic**, with a fixed geographic origin and deterministic seed. Lane details, signals, and vehicle motions are not reconstructed from camera footage. It has 24 surrounding vehicles and one ego SUV over a 60-second loop. No real-world safety or congestion measurements are claimed.

OpenFreeMap, Google Fonts, DOT, and 511NY require internet access. System fonts remain usable if font loading fails. A local dark map style preserves the authored scene if city context is unavailable. Camera failures show Retry and preserve the rest of the workspace.

## Architecture

See the detailed [architecture guide](docs/ARCHITECTURE.md), [camera API reference](docs/CAMERA_API.md), and [verification record](docs/VERIFICATION.md).

- React owns selection, presentation, and controls. One replay clock samples the deterministic run; the renderer reads frame and option refs without React updates for each display frame.
- MapLibre owns geographic projection and the shared WebGL context. A custom Three.js layer draws locally metric roads, instanced traffic, and the detailed ego vehicle. Local axes are east/north/up; headings are radians counterclockwise from east.
- `ScenarioProvider.loadScenario()` returns the typed `ScenarioBundle`. `DemoScenarioProvider` is the initial implementation. A later backend adapter can supply compatible scene geometry and timestamped frames without rewriting the view.
- Camera selection is separate from scene identity. Roadside video is an HTML layer above the map, not a calibrated projection onto it.
- FastAPI supplies camera metadata, a DOT cache, bounded media transport, capture services, and model gateway status. HLS playlists resolve server-owned camera IDs and opaque resource tokens; arbitrary URLs are not accepted. The model pipeline is not yet wired into the authored demo UI.

Public endpoints: `GET /api/health`, `GET /api/cameras`, `GET /api/vision/cameras`, and `/api/feeds/{camera_id}/index.m3u8` with tokenized child resources. See the local `/docs` route for exact schemas.

## Verify

```sh
npm run contracts:check
npm run build
npm test
npm run test:api
```

Frontend tests cover deterministic sampling, backwards seeking, interpolation, lane separation, bounded motion, and signal stops. Backend tests cover registry identity, stale state, playlist rewriting, resource validation, redirects, ranges, and startup without model credentials.

For a production-style local preview, run `npm run build`, then start `npm run dev:api` and open `http://127.0.0.1:8000`. FastAPI serves the built application when `dist` exists at startup. Alternatively, `npm run preview` uses the same API proxy with the API running.

Implementation verification: production build, six scenario tests, and 19 API tests passed. Desktop (1440 × 900) and mobile (390 × 844) views were reviewed in-browser. Vestry HLS video visibly advanced through the local proxy. Camera changes, feed toggling, seeking, layer switches, and City/Intersection/Follow transitions were exercised; the active page kept at most one video element. Chrome reported approximately 120 map render events per second in Follow with one playing feed on the development machine; this is an observed local result, not a cross-device guarantee.

The contracts, gateway, capture, and observation services are covered by automated tests. The primary project's live capability checks and real video/snapshot observation runs are recorded in [verification](docs/VERIFICATION.md). Full extraction/edit/image/world integration, Mayor Mode UI, human annotation, and independent evaluation remain acceptance work in [the Element 119 plan](GOTHAM_FORGE_PLAN.md). SUMO and calibrated reconstruction are later milestones.
