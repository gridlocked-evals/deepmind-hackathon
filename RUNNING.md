# Gotham Forge

A geographic simulation workspace for New York: a 3D city map, an illustrative Vestry Street traffic scene, and independently advancing public camera footage.

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
- FastAPI contains only camera metadata, a DOT cache, and bounded media transport. HLS playlists resolve server-owned camera IDs and opaque resource tokens; arbitrary URLs are not accepted.

Public endpoints: `GET /api/health`, `GET /api/cameras`, `GET /api/vision/cameras`, and `/api/feeds/{camera_id}/index.m3u8` with tokenized child resources. See the local `/docs` route for exact schemas.

## Verify

```sh
npm run build
npm test
npm run test:api
```

Frontend tests cover deterministic sampling, backwards seeking, interpolation, lane separation, bounded motion, and signal stops. Backend tests cover registry identity, stale state, playlist rewriting, resource validation, redirects, ranges, and startup without model credentials.

For a production-style local preview, run `npm run build`, then start `npm run dev:api` and open `http://127.0.0.1:8000`. FastAPI serves the built application when `dist` exists at startup. Alternatively, `npm run preview` uses the same API proxy with the API running.

Implementation verification: production build, six scenario tests, and 19 API tests passed. Desktop (1440 × 900) and mobile (390 × 844) views were reviewed in-browser. Vestry HLS video visibly advanced through the local proxy. Camera changes, feed toggling, seeking, layer switches, and City/Intersection/Follow transitions were exercised; the active page kept at most one video element. Chrome reported approximately 120 map render events per second in Follow with one playing feed on the development machine; this is an observed local result, not a cross-device guarantee.

Gemini/SUMO, footage capture, calibrated reconstruction, generated video, intervention search, and evaluation remain future integration work described in `GOTHAM_FORGE_PLAN.md`.
