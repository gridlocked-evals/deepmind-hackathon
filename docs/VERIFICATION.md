# Citywide camera and navigation verification

Verified November 21, 2026, on the UI branch based on `6ec141c`.

## Automated checks

- `npm run build` passes TypeScript and the production build. Vite retains a bundle-size advisory for the MapLibre/Three.js application bundle.
- `npm test`: 22 passing tests across authored traffic, geographic route motion, routing validation/cancellation, and camera-directory reconciliation.
- `npm run test:api`: 87 passing backend tests, including all five boroughs, duplicate/invalid camera rejection, cache behavior, media proxy, gateway, and world runtime.
- `git diff --check` passes.

Route regression tests check geographic alignment against MapLibre across borough-scale distances, bounded acceleration and braking, slowdown before a 90-degree turn, exact arrival at zero speed, deterministic seeking, bounded frame allocation, and cancellation of stale responses.

## Live sources and browser checks

The official DOT directory returned 964 records: Manhattan 376, Brooklyn 209, Queens 211, Bronx 68, and Staten Island 100. All normalized successfully. Together with five configured 511NY video sources, the UI showed 969 records. This is directory coverage, not a count of currently advancing feeds, and will change with the upstream directory.

Browser checks at 1440 × 950 and 390 × 844 confirmed:

- Full directory counts, borough filtering, search, map clusters, and pagination past the former 80-camera cutoff. Staten Island displayed 100 cameras and page two showed records 41–80.
- Vestry video visibly advancing through the existing player; hiding/collapsing the feed preserved navigation.
- A map click at Greenwich Street and Desbrosses Street resolved both street names. Start here launched a new local preview at that intersection with the unverified-direction label.
- A live OSRM driving route from Vestry to Houston returned street geometry, distance, duration estimate, and directions. Drive this route started at the route origin; Follow tracked the vehicle on the street.
- Seeking to arrival stopped playback with zero speed. Clearing the planner removed the previous journey and returned to City.
- An upstream routing timeout showed a retryable error; retry successfully returned a route.
- Mobile controls and planner remained accessible with no horizontal document overflow.

The map was also checked for correct scene replacement, inset handling, and route depth rendering. No paid model calls, generated worlds, or image inference were needed.

## Accuracy boundaries

Routing uses OpenStreetMap/OSRM road geometry and mapped driving directions. Speeds are a deterministic illustration with an assumed cruise cap, rather than a forecast or observed traffic. Live signal timing, lane calibration, elevation, surrounding actors, and image-derived observations are not supplied. A clicked-street preview has unverified driving direction; a camera location without road geometry stays stationary until a route is selected. These distinctions are visible in the UI.

The public routing service and camera providers require network access. The UI preserves saved camera records across refresh failures, while a first offline startup has only the configured video catalog.
