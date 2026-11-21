# Gotham Forge architecture

Gotham Forge currently combines a geographic NYC map, one deterministic traffic demonstration, and independently advancing public camera footage. The frontend runs without model credentials. The FastAPI backend supplies camera metadata and HLS transport; it does not execute the traffic simulation.

For setup and controls, see [RUNNING.md](../RUNNING.md). The broader reconstruction and evaluation roadmap remains in [GOTHAM_FORGE_PLAN.md](../GOTHAM_FORGE_PLAN.md).

## Contracts and ownership

Shared frontend contracts for the current map/demo live in [src/types.ts](../src/types.ts). The Element 119 submission pipeline (capture → Hydrogen → Carbon → Nano Banana → Decisions → Genie → evaluation) uses the separate versioned contracts in [CONTRACTS.md](CONTRACTS.md); its schema is served at `GET /api/contracts/element119`.

| Contract | Responsibility |
| --- | --- |
| `CameraSource` | Source identity, actual geographic coordinates, HLS/snapshot kind, playback and attribution URLs, and availability metadata. |
| `ScenarioBundle` | Scenario ID/version, provenance, seed, associated camera, geographic origin, local geometry, actor definitions, and timestamped frames. |
| `SimulationFrame` | Actor poses, speeds, braking flags, and the scene's single signal state at one simulation time. |
| `ScenarioProvider` | Asynchronous `loadScenario(id, signal)` boundary for obtaining a bundle. `DemoScenarioProvider` implements it. |
| `SceneRuntime` | Mutable frame and render-option refs shared by playback, the map, and the Three.js layer. |

`App` owns UI selection, view mode, feed placement, layer visibility, and playback controls. It currently imports the already-created `demoScenario` directly; provider-based loading is an extension point, not an active backend connection. `MapViewport` reports dimensions and a projected camera position for the HTML feed card.

Coordinates are explicit: geographic tuples are `[longitude, latitude]`; local distances are meters, with x east, y north, and z up. Actor headings are radians counterclockwise from east. Simulation timestamps and speeds use seconds and meters per second. The HUD converts speed to mph for display.

## Replay and time

[DemoScenarioProvider](../src/scene/DemoScenarioProvider.ts) creates a seeded, 60-second run with 24 surrounding vehicles and one ego vehicle. States are precomputed at 0.1-second intervals using authored lane-following and signal behavior. These trajectories are an illustration, not a fit to observed footage.

`sampleScenario` is a pure random-access sampler. It locates neighboring frames, interpolates positions/speeds and shortest-path headings, and holds the preceding discrete signal state. Boundary re-entry is treated as a discontinuity rather than interpolating a vehicle across the intersection. Seeking never integrates or mutates the saved run.

[usePlayback](../src/hooks/usePlayback.ts) owns the demo clock. Its animation loop advances relative simulation time at the selected rate and wraps at the scenario duration. It writes sampled frames into `frameRef`; React's displayed time updates approximately every 80 ms. Pausing and hidden-page handling stop progression. Seeking clamps to the available interval and immediately updates both the frame ref and displayed time.

The live player has its own media clock. Demo pause, speed changes, seeking, view changes, and camera selection do not synchronize or rewind live footage. Snapshot load times indicate retrieval, not original exposure time; broadcast delay and source capture clocks remain unknown.

## Geographic and local rendering

[CityMap](../src/map/CityMap.tsx) owns MapLibre, its geographic camera, the canvas, and the repaint loop. OpenFreeMap/OpenStreetMap provide city context and building extrusions. A local dark style initializes immediately and retains the authored scene if the remote style is unavailable. The MapLibre ESM worker is explicitly bundled through Vite.

[SimulationLayer](../src/scene/SimulationLayer.ts) creates a `renderingMode: '3d'` custom layer in that same WebGL context. It transforms local metric geometry into normalized Mercator coordinates, reversing the local north axis to match Mercator's south-pointing y axis. The Three.js projection uses MapLibre's `defaultProjectionData.mainMatrix`; the alternative `modelViewProjectionMatrix` uses different internal units.

MapLibre owns camera motion and canvas sizing. Three.js leaves `autoClear` disabled, preserves the active framebuffer across its state reset, and participates in the shared depth buffer. It renders the local scene after geographic layers. Basemap replacement deliberately uses `setStyle(..., { diff: false })`: MapLibre's style serialization omits custom layers, so a style diff could retain the old simulation beneath the replacement basemap. Each new style recreates the custom layer and camera markers; removal disposes Three.js resources.

Traffic parts and road paint use instancing, and static meshes are merged by material. The local scene skips drawing below zoom 16.5; pixel density is capped at 1.5. Frame/option refs keep per-frame rendering outside React. DOM diagnostic attributes report measured render activity and draw information without adding simulated performance figures to the UI.

## Views and camera sources

City, Intersection, and Follow are camera presentations of the same run. Follow reads the selected actor from the shared frame and smoothly updates the geographic camera. Manual map movement releases Follow without restarting the scenario. `viewRevision` makes selecting the current view again an explicit recenter action.

Source selection and scenario identity stay separate. Choosing a roadside camera moves the city focus to that source's actual coordinates. Entering Intersection or Follow explicitly restores the Vestry scene and its associated camera. It does not turn another source's footage into evidence for the demo.

Feeds are HTML surfaces above the map: an anchored City card or a bottom dock, with responsive placement. They are not video textures projected onto streets. The application mounts one selected `LiveFeed`; hiding or collapsing it unmounts the player and releases playback resources. Layout measurements reserve space for the rail, heading, dock, and timeline. Padding updates wait for active camera movement to finish so opening a dock cannot cancel a view transition.

## Camera API and media transport

The API exposes `GET /api/health`, `/api/cameras` for Manhattan DOT snapshots, `/api/vision/cameras` for configured 511NY video sources, and `/api/feeds/{camera_id}/index.m3u8` plus tokenized child resources. Development uses Vite's API proxy. FastAPI can also serve `dist/` when that build exists at startup.

The DOT directory cache retains the last successful fetch time when serving stale data. Snapshot images load directly from validated DOT URLs. HLS requests use the local proxy first; the frontend can fall back to a known direct stream URL. Native HLS is preferred when supported, otherwise `hls.js` is loaded dynamically. Playback has bounded retries, explicit error states, visibility-aware suspension, and cleanup when the source changes.

The HLS proxy accepts registered camera IDs rather than arbitrary upstream URLs. It validates source host/path and redirects, rewrites playlist URIs to camera-bound opaque tokens, supports single byte ranges, and bounds concurrency and response sizes. Tokens, source health, and caches are in memory. A server restart discards them; there is no footage archive or persistent episode store.

## Extension points and current limits

- Add a backend `ScenarioProvider`, runtime payload validation, and loading/error state in `App` to replace its static bundle import. Preserve the metric axes, actor IDs, ordered frame times, provenance, and version identity.
- Generalize the renderer before accepting arbitrary intersections. Base polygons and actor states come from the bundle, but curbs, markings, signal placement, furniture, and route-progress coloring still use Vestry-specific constants. The renderer currently supports one ego plus up to 63 surrounding actors, three vehicle kinds, and one scene-wide signal state.
- Add capture and replay contracts before claiming video/simulation synchronization. Camera identity alone is insufficient: captured media, calibration, scene/branch version, and relative time must be joined explicitly.
- Gemini reconstruction, SUMO execution, calibrated geometry, generated sensor video, interventions, and safety evaluation are not implemented. Geographic context and plausible animation do not establish observed traffic behavior or real-world safety.

Scenario tests cover deterministic replay, seeking, interpolation discontinuities, lane separation, and signal stopping. Backend tests cover source normalization, stale timestamps, playlist rewriting, allowlists, redirects, ranges, and credential-free startup. Browser checks remain necessary for projection/depth, view transitions, responsive overlays, and actual source playback; see [RUNNING.md](../RUNNING.md) for commands and recorded verification.
