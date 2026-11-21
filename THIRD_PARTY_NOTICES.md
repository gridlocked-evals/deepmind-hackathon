# Gotham Forge — sources and adaptation

The application adapts the geographic map and public camera playback architecture of [Divyesh-Thirukonda/pharos](https://github.com/Divyesh-Thirukonda/pharos), pinned at commit `5bd6fd9ba4dcd206c96e5c9c15f37b96fcf42f1a`. Its city map, HLS lifecycle, camera registry, and base-path handling were inspected and adapted into this implementation. Research documents already in this repository are preserved.

The new simulation, procedural vehicle models, playback controls, dark visual design, and camera-only backend are authored for Gotham Forge. Waymo's World Model and the user-supplied image are visual references; this application does not use or claim access to Waymo models or assets.

## Migration inventory

| Upstream responsibility | Gotham Forge implementation |
| --- | --- |
| CityMap geographic view, camera selection, style | `src/map/` |
| LiveObserver's LivePlayer lifecycle | `src/media/LiveFeed.tsx` |
| Camera/LiveCamera and API base handling | `src/types.ts`, `src/media/cameras.ts` |
| Public DOT directory and trusted HLS registry | `backend/main.py`, `backend/feeds.py` |
| No upstream traffic simulation | New `src/scene/` |

Legacy inference, sponsor services, archive search, and crash analysis have not been imported into this first UI slice.

No upstream license grant is inferred from public availability. The original Pharos notices follow unchanged below. Links in that original section refer to paths in the upstream repository.

---

# Third party notices

## PharOS Elmir layout reference

The map-first workspace layout and interface organization were adapted with reference to [elmir132/pharos-elmir](https://github.com/elmir132/pharos-elmir), specifically commit [`c4bc68b8261542d78b35ca6147e863ecfedf9a4f`](https://github.com/elmir132/pharos-elmir/tree/c4bc68b8261542d78b35ca6147e863ecfedf9a4f), inspected on October 9, 2026.

The relevant source is [`web/pharos/index.html`](https://github.com/elmir132/pharos-elmir/blob/c4bc68b8261542d78b35ca6147e863ecfedf9a4f/web/pharos/index.html), with design context in [`design/README.md`](https://github.com/elmir132/pharos-elmir/blob/c4bc68b8261542d78b35ca6147e863ecfedf9a4f/design/README.md). The adapted elements are the full-screen map, compact map controls, right-hand tabbed workspace, and the progression from city history to a camera area and recorded-video review. They are implemented here in React and styled with PharOS's emerald, sage, and ivory palette. This project's original headdress/eye mark was revised to add a geometric lighthouse.

No `LICENSE`, `COPYING`, or `NOTICE` file, or explicit license declaration in the inspected README/interface source, was found at that reference revision. This attribution does not assert an open-source license or create a license grant. Rights in the reference material remain with its authors.

## Maps, data and dependencies

Map rendering uses MapLibre with OpenFreeMap/OpenStreetMap data; map attribution remains visible in the interface. Camera video and images are provided by 511NY/NYSDOT and NYC DOT. Historical crash records come from NYC Open Data. Their source links and access conditions are recorded in [data sources](design/data-sources.md).

Driving routes use the [OSRM Route API](https://project-osrm.org/docs/v5.24.0/api/#route-service) via the public `router.project-osrm.org` demonstration service, using [OpenStreetMap contributor data](https://www.openstreetmap.org/copyright). OSRM provides street geometry and a routing estimate; this app generates its own illustrative vehicle motion. The selected endpoint coordinates are sent directly to that service when the user requests a route. Access is best-effort and subject to the [OSRM demo-service usage policy](https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy); sustained production use should use an appropriately provisioned routing service. Basemap-only local previews are distinct from OSRM driving routes and do not verify directionality.

The NYC DOT directory is loaded from its [official camera endpoint](https://webcams.nyctmc.org/api/cameras). [NYC DOT describes these sources](https://www.nyc.gov/html/dot/html/motorist/atis.shtml) as frequently updated still images across the five boroughs. Camera coverage and availability change over time.

Third-party packages retain their own notices and license terms. This document records the interface adaptation and source attribution; it does not replace notices distributed with installed dependencies or grant rights to provider footage.
