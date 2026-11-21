import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import type { GeoJSONSource, Map as MapInstance, StyleSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import { createSimulationLayer } from '../scene/SimulationLayer';
import type { NavigationPoint, PlannedRoute } from '../navigation/types';
import type { Feature, FeatureCollection } from 'geojson';
import type { CameraSource, LngLat, MapViewport, ScenarioBundle, SceneRuntime, ViewMode } from '../types';
import { fallbackStyle, gothamStyle, MAP_STYLE_URL } from './mapStyle';
import './CityMap.css';

// MapLibre 6 ships an ESM worker; bundling it explicitly preserves its URL in Vite.
maplibregl.setWorkerUrl(mapWorkerUrl);

export interface CityMapProps {
  scenario: ScenarioBundle;
  runtime: SceneRuntime;
  viewMode: ViewMode;
  selectedCamera: CameraSource | null;
  cameras: CameraSource[];
  sidebarOpen: boolean;
  feedsVisible: boolean;
  viewRevision?: number;
  navigationStart?: NavigationPoint | null;
  navigationEnd?: NavigationPoint | null;
  plannedRoute?: PlannedRoute | null;
  pickingMode?: 'inspect' | 'start' | 'destination';
  focusLocation?: NavigationPoint | null;
  focusRevision?: number;
  onLocationSelect?(point: NavigationPoint): void;
  onCameraSelect(cameraId: string): void;
  onActorSelect(actorId: string): void;
  onFollowExit(): void;
  onViewport(view: MapViewport): void;
  onReady?(): void;
  onError?(message: string): void;
}

const EMPTY_POINTS = { type: 'FeatureCollection' as const, features: [] };
const CAMERA_LAYERS = ['camera-hit', 'selected-camera-hit'];
const CITY_CAMERA = { center: [-73.98, 40.705] as LngLat, zoom: 10, pitch: 0, bearing: 0 };
const NYC_BOUNDS: [LngLat, LngLat] = [[-74.26, 40.49], [-73.70, 40.92]];
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const toBearing = (heading: number) => 90 - heading * 180 / Math.PI;

function geographicPoint(origin: LngLat, x: number, y: number): LngLat {
  const anchor = maplibregl.MercatorCoordinate.fromLngLat(origin);
  const scale = anchor.meterInMercatorCoordinateUnits();
  const point = new maplibregl.MercatorCoordinate(anchor.x + x * scale, anchor.y - y * scale).toLngLat();
  return [point.lng, point.lat];
}

function framePadding(map: MapInstance, props: CityMapProps) {
  const container = map.getContainer();
  const bounds = container.getBoundingClientRect();
  // During a ResizeObserver turn the DOM and the map transform can differ briefly.
  const width = Math.max(1, Math.min(bounds.width, map.getCanvas().clientWidth || bounds.width));
  const height = Math.max(1, Math.min(bounds.height, map.getCanvas().clientHeight || bounds.height));
  const workspace = container.closest('.workspace');
  const rail = workspace?.querySelector('.source-rail')?.getBoundingClientRect();
  const playback = workspace?.querySelector('.playback-bar')?.getBoundingClientRect();
  const dock = workspace?.querySelector('.feed-surface.feed-docked')?.getBoundingClientRect();
  const heading = workspace?.querySelector('.scene-heading')?.getBoundingClientRect();
  const header = workspace?.querySelector('.topbar')?.getBoundingClientRect();
  const top = Math.max((header?.bottom ?? bounds.top + 62) - bounds.top + 14,
    heading ? heading.bottom - bounds.top + 20 : 0);
  const bottom = Math.max(playback ? bounds.bottom - playback.top + 14 : 88,
    dock ? bounds.bottom - dock.top + 14 : 0);
  return {
    left: props.sidebarOpen && width > 760 && rail ? Math.min(width * 0.45, Math.max(0, rail.right - bounds.left)) : 0,
    right: width > 760 ? 28 : 0,
    top: Math.min(top, height * 0.34),
    bottom: Math.min(bottom, height * 0.48),
  };
}

function setControlInsets(map: MapInstance, padding: ReturnType<typeof framePadding>) {
  const root = map.getContainer().parentElement;
  root?.style.setProperty('--map-navigation-bottom', `${Math.max(68, padding.bottom)}px`);
  root?.style.setProperty('--map-status-bottom', `${padding.bottom + 4}px`);
}

function cameraData(cameras: CameraSource[]) {
  return {
    type: 'FeatureCollection' as const,
    features: cameras.map(camera => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: camera.coordinates },
      properties: {
        id: camera.id,
        name: camera.name,
        kind: camera.kind,
        availability: camera.availability,
      },
    })),
  };
}

function addCameraLayers(map: MapInstance) {
  map.addSource('gotham-cameras', { type: 'geojson', data: EMPTY_POINTS, cluster: true, clusterRadius: 44, clusterMaxZoom: 14 });
  map.addSource('gotham-selected-camera', { type: 'geojson', data: EMPTY_POINTS });
  map.addLayer({
    id: 'camera-clusters', type: 'circle', source: 'gotham-cameras', filter: ['has', 'point_count'],
    paint: { 'circle-radius': ['step', ['get', 'point_count'], 20, 25, 25, 100, 31],
      'circle-color': '#193f43', 'circle-stroke-color': '#79d7c5', 'circle-stroke-width': 1.5 },
  });
  map.addLayer({
    id: 'camera-cluster-count', type: 'symbol', source: 'gotham-cameras', filter: ['has', 'point_count'],
    layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['Noto Sans Regular'], 'text-size': 13 },
    paint: { 'text-color': '#d6fff2' },
  });
  map.addLayer({
    id: 'camera-glow', type: 'circle', source: 'gotham-cameras',
    filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'kind'], 'hls']],
    paint: { 'circle-radius': 17, 'circle-color': '#7df5d3', 'circle-opacity': 0.12, 'circle-blur': 0.7 },
  });
  map.addLayer({
    id: 'camera-points', type: 'circle', source: 'gotham-cameras', filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-radius': ['case', ['==', ['get', 'kind'], 'hls'], 5.5, ['interpolate', ['linear'], ['zoom'], 11, 2, 16, 4]],
      'circle-color': ['case', ['==', ['get', 'kind'], 'hls'], '#85eed2', '#213846'],
      'circle-stroke-width': ['case', ['==', ['get', 'kind'], 'hls'], 1.5, 1],
      'circle-stroke-color': ['case', ['==', ['get', 'kind'], 'hls'], '#ddfff5', '#587684'],
      'circle-opacity': ['case', ['==', ['get', 'availability'], 'unavailable'], 0.4, 0.95],
    },
  });
  map.addLayer({
    id: 'camera-labels', type: 'symbol', source: 'gotham-cameras', minzoom: 16,
    filter: ['!', ['has', 'point_count']],
    layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 11, 'text-anchor': 'top', 'text-offset': [0, 1.3], 'text-max-width': 17 },
    paint: { 'text-color': '#9cb9c5', 'text-halo-color': '#07121b', 'text-halo-width': 2 },
  });
  map.addLayer({ id: 'camera-hit', type: 'circle', source: 'gotham-cameras', filter: ['!', ['has', 'point_count']], paint: { 'circle-radius': 15, 'circle-opacity': 0 } });
  map.addLayer({
    id: 'selected-camera-halo', type: 'circle', source: 'gotham-selected-camera',
    paint: { 'circle-radius': 25, 'circle-color': '#7df5d3', 'circle-opacity': 0.075, 'circle-stroke-width': 1, 'circle-stroke-color': '#8adbc8', 'circle-stroke-opacity': 0.45 },
  });
  map.addLayer({
    id: 'selected-camera-core', type: 'circle', source: 'gotham-selected-camera',
    paint: { 'circle-radius': 7, 'circle-color': '#a4ffe1', 'circle-stroke-width': 3, 'circle-stroke-color': '#153c38' },
  });
  map.addLayer({ id: 'selected-camera-hit', type: 'circle', source: 'gotham-selected-camera', paint: { 'circle-radius': 25, 'circle-opacity': 0 } });
}

/** Pick the road geometry actually visible under the pointer, never an invented grid. */
function streetAt(map: MapInstance, pointer: maplibregl.Point): NavigationPoint | null {
  if (map.getZoom() < 13) return null;
  const layers = map.getStyle().layers.filter(layer =>
    layer.type === 'line' && /road|bridge|tunnel/.test(layer.id) && !/rail|path|ferry/.test(layer.id)).map(layer => layer.id);
  if (!layers.length) return null;
  const features: Pick<maplibregl.GeoJSONFeature, 'geometry' | 'properties'>[] = map.queryRenderedFeatures([[pointer.x - 24, pointer.y - 24], [pointer.x + 24, pointer.y + 24]], { layers });
  const roadFeatureCount = features.length;
  // OpenMapTiles keeps names in a separate source layer; road paint often has none.
  const namedLayers = map.getStyle().layers.filter(layer => layer.type === 'symbol'
    && 'source-layer' in layer && layer['source-layer'] === 'transportation_name');
  for (const source of new Set(namedLayers.map(layer => 'source' in layer ? layer.source : '').filter(Boolean))) {
    features.push(...map.querySourceFeatures(source, { sourceLayer: 'transportation_name' }));
  }
  type Candidate = { distance: number; coordinates: LngLat; path: LngLat[]; label: string; painted: boolean };
  const candidates: Candidate[] = [];
  for (const [featureIndex, feature] of features.entries()) {
    if (/path|footway|pedestrian|cycleway|steps|rail/.test(String(feature.properties?.class ?? ''))) continue;
    const lines = feature.geometry.type === 'LineString' ? [feature.geometry.coordinates]
      : feature.geometry.type === 'MultiLineString' ? feature.geometry.coordinates : [];
    for (const line of lines) {
      for (let i = 1; i < line.length; i++) {
        const a = line[i - 1] as LngLat, b = line[i] as LngLat;
        const pa = map.project(a), pb = map.project(b);
        const dx = pb.x - pa.x, dy = pb.y - pa.y;
        const fraction = Math.max(0, Math.min(1, ((pointer.x - pa.x) * dx + (pointer.y - pa.y) * dy) / (dx * dx + dy * dy || 1)));
        const distance = Math.hypot(pointer.x - pa.x - dx * fraction, pointer.y - pa.y - dy * fraction);
        if (distance > 24) continue;
        // Use geographic interpolation to keep the selected point on the source polyline.
        const coordinates: LngLat = [a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction];
        const reverse = feature.properties?.oneway === -1 || feature.properties?.oneway === '-1';
        const path = [coordinates, ...(reverse ? line.slice(0, i).reverse() : line.slice(i)) as LngLat[]]
          .filter((point, index, points) => index === 0 || point[0] !== points[index - 1][0] || point[1] !== points[index - 1][1]);
        const name = feature.properties?.['name:en'] || feature.properties?.name || feature.properties?.ref;
        candidates.push({ distance, coordinates, path, label: typeof name === 'string' ? name : 'Selected street', painted: featureIndex < roadFeatureCount });
      }
    }
  }
  candidates.sort((a, b) => a.distance - b.distance || b.path.length - a.path.length);
  const closest = candidates.find(candidate => candidate.painted);
  if (!closest) return null;
  const metresBetween = (candidate: Candidate) => Math.hypot((candidate.coordinates[0] - closest.coordinates[0]) * 84_000,
    (candidate.coordinates[1] - closest.coordinates[1]) * 111_000);
  const named = candidates.find(candidate => candidate.label !== 'Selected street' && metresBetween(candidate) < 6);
  const label = named?.label ?? closest.label;
  const crossing = candidates.find(candidate => candidate.label !== label && candidate.label !== 'Selected street'
    && candidate.distance < 10 && closest.distance < 10 && metresBetween(candidate) < 12);
  return { coordinates: closest.coordinates, label: crossing ? `${label} & ${crossing.label}` : label,
    streetPath: closest.path.length > 1 ? closest.path : undefined };
}

function navigationData(props: CityMapProps): FeatureCollection {
  const features: Feature[] = [];
  if (props.plannedRoute?.coordinates.length) features.push({ type: 'Feature', properties: { kind: 'route' },
    geometry: { type: 'LineString', coordinates: props.plannedRoute.coordinates } });
  const points = [[props.navigationStart, 'start', 'A'], [props.navigationEnd, 'destination', 'B'],
    [props.focusLocation, 'selected', '']] as const;
  for (const [point, kind, marker] of points) {
    if (!point) continue;
    if (kind === 'selected' && [props.navigationStart, props.navigationEnd].some(endpoint => endpoint
      && endpoint.coordinates[0] === point.coordinates[0] && endpoint.coordinates[1] === point.coordinates[1])) continue;
    features.push({ type: 'Feature', properties: { kind, marker, label: point.label },
      geometry: { type: 'Point', coordinates: point.coordinates } });
  }
  return { type: 'FeatureCollection', features };
}

function addNavigationLayers(map: MapInstance) {
  map.addSource('gotham-navigation', { type: 'geojson', data: EMPTY_POINTS });
  map.addLayer({ id: 'navigation-route-outline', type: 'line', source: 'gotham-navigation',
    filter: ['==', ['get', 'kind'], 'route'], layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-width': 8, 'line-color': '#052f32', 'line-opacity': 0.9 } });
  map.addLayer({ id: 'navigation-route', type: 'line', source: 'gotham-navigation',
    filter: ['==', ['get', 'kind'], 'route'], layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-width': 4, 'line-color': '#80e7cb', 'line-opacity': 0.9 } });
  map.addLayer({ id: 'navigation-endpoints', type: 'circle', source: 'gotham-navigation',
    filter: ['!=', ['get', 'kind'], 'route'],
    paint: { 'circle-radius': ['case', ['==', ['get', 'kind'], 'selected'], 7, 13],
      'circle-color': ['case', ['==', ['get', 'kind'], 'destination'], '#f4c583', '#9df1d4'],
      'circle-stroke-color': '#0a2229', 'circle-stroke-width': 3 } });
  map.addLayer({ id: 'navigation-endpoint-letters', type: 'symbol', source: 'gotham-navigation',
    filter: ['!=', ['get', 'kind'], 'route'],
    layout: { 'text-field': ['get', 'marker'], 'text-font': ['Noto Sans Regular'], 'text-size': 12, 'text-allow-overlap': true },
    paint: { 'text-color': '#08232b' } });
}

export default function CityMap(props: CityMapProps) {
  const routesVisible = props.runtime.optionsRef.current.routesVisible;
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const layerRef = useRef<ReturnType<typeof createSimulationLayer> | null>(null);
  const followAfter = useRef(0);
  const manuallyReleasedFollow = useRef(false);
  const publishViewport = useRef<() => void>(() => {});
  const reloadBasemap = useRef<() => void>(() => {});
  const [ready, setReady] = useState(false);
  const [basemapStatus, setBasemapStatus] = useState<'loading' | 'connected' | 'unavailable'>('loading');
  const [webglError, setWebglError] = useState(false);
  const installedScenario = useRef(`${props.scenario.id}:${props.scenario.version}`);

  useEffect(() => {
    if (!element.current) return;
    let disposed = false;
    let animation = 0;
    let lastFollow = 0;
    let lastRepaint = 0;
    let lastRenderedFrame: object | null = null;
    let lastRenderedOptions = '';
    let previousViewport: MapViewport | null = null;
    let styleRequest: AbortController | null = null;
    let styleTimer = 0;
    let resizeObserver: ResizeObserver | null = null;
    let layoutObserver: MutationObserver | null = null;
    let layoutFrame = 0;
    let lastContainerWidth = 0;
    let lastContainerHeight = 0;
    let renderedFrames = 0;
    let lastFpsSample = performance.now();
    let deferredLayout = false;
    let map: MapInstance;
    try {
      map = new maplibregl.Map({
        container: element.current,
        style: fallbackStyle(),
        ...CITY_CAMERA,
        minZoom: 8.5,
        maxZoom: 21.6,
        maxPitch: 80,
        renderWorldCopies: false,
        attributionControl: false,
        pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
        canvasContextAttributes: { antialias: true },
      });
    } catch {
      setWebglError(true);
      latest.current.onError?.('WebGL is unavailable. Camera feeds and simulation controls remain accessible.');
      return;
    }
    mapRef.current = map;
    lastContainerWidth = map.getContainer().clientWidth;
    lastContainerHeight = map.getContainer().clientHeight;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

    const publish = () => {
      if (disposed) return;
      const selected = latest.current.viewMode === 'city' ? latest.current.selectedCamera : null;
      const point = selected ? map.project(selected.coordinates) : null;
      const next: MapViewport = {
        width: map.getContainer().clientWidth,
        height: map.getContainer().clientHeight,
        cameraPoint: point && Number.isFinite(point.x) && Number.isFinite(point.y)
          ? { x: Math.round(point.x * 2) / 2, y: Math.round(point.y * 2) / 2 }
          : null,
      };
      if (previousViewport?.width === next.width && previousViewport.height === next.height
        && previousViewport.cameraPoint?.x === next.cameraPoint?.x && previousViewport.cameraPoint?.y === next.cameraPoint?.y) return;
      previousViewport = next;
      latest.current.onViewport(next);
    };
    publishViewport.current = publish;

    const updateSources = () => {
      (map.getSource('gotham-cameras') as GeoJSONSource | undefined)?.setData(cameraData(latest.current.cameras));
      (map.getSource('gotham-selected-camera') as GeoJSONSource | undefined)?.setData(cameraData(latest.current.selectedCamera ? [latest.current.selectedCamera] : []));
      (map.getSource('gotham-navigation') as GeoJSONSource | undefined)?.setData(navigationData(latest.current));
    };
    map.on('style.load', () => {
      if (disposed) return;
      map.setLight({ anchor: 'map', color: '#c2deef', intensity: 0.46, position: [1.6, 190, 32] });
      const layer = createSimulationLayer(latest.current.scenario, latest.current.runtime);
      layerRef.current = layer;
      installedScenario.current = `${latest.current.scenario.id}:${latest.current.scenario.version}`;
      map.addLayer(layer);
      addCameraLayers(map);
      addNavigationLayers(map);
      for (const id of ['navigation-route', 'navigation-route-outline']) {
        map.setLayoutProperty(id, 'visibility', latest.current.runtime.optionsRef.current.routesVisible && latest.current.viewMode === 'city' ? 'visible' : 'none');
      }
      for (const layer of map.getStyle().layers) {
        if ('source' in layer && (layer.source === 'gotham-cameras' || layer.source === 'gotham-selected-camera')) {
          map.setLayoutProperty(layer.id, 'visibility', latest.current.viewMode === 'city' ? 'visible' : 'none');
        }
      }
      updateSources();
      const padding = framePadding(map, latest.current);
      if (!map.isMoving()) map.setPadding(padding);
      setControlInsets(map, padding);
      setReady(true);
      latest.current.onReady?.();
      publish();
    });

    const cameraAt = (point: maplibregl.Point) => {
      if (!map.getLayer('camera-hit')) return null;
      const features = map.queryRenderedFeatures([[point.x - 3, point.y - 3], [point.x + 3, point.y + 3]], { layers: CAMERA_LAYERS });
      const feature = features.find(item => item.layer.id === 'selected-camera-hit') ?? features[0];
      return typeof feature?.properties?.id === 'string' ? feature.properties.id : null;
    };
    const clusterAt = (point: maplibregl.Point) => map.getLayer('camera-clusters')
      ? map.queryRenderedFeatures(point, { layers: ['camera-clusters'] })[0] : undefined;
    map.on('click', event => {
      const cluster = clusterAt(event.point);
      if (cluster && cluster.geometry.type === 'Point') {
        const source = map.getSource('gotham-cameras') as GeoJSONSource;
        const coordinates = cluster.geometry.coordinates as LngLat;
        void source.getClusterExpansionZoom(Number(cluster.properties.cluster_id)).then(zoom => {
          if (!disposed && map.getSource('gotham-cameras') === source) {
            map.easeTo({ center: coordinates, zoom, duration: reducedMotion() ? 0 : 350 });
          }
        }).catch(() => {}); // A directory/style refresh can invalidate an in-flight cluster.
        return;
      }
      const picking = latest.current.pickingMode && latest.current.pickingMode !== 'inspect';
      const actorId = !picking && layerRef.current?.pickActor(event.point);
      if (actorId) { latest.current.onActorSelect(actorId); return; }
      const cameraId = cameraAt(event.point);
      if (cameraId) {
        const source = latest.current.cameras.find(camera => camera.id === cameraId);
        if (picking && source) {
          const street = streetAt(map, map.project(source.coordinates));
          latest.current.onLocationSelect?.({ ...street, coordinates: street?.coordinates ?? source.coordinates, label: source.name });
        } else {
          latest.current.onCameraSelect(cameraId);
          if (source) {
            const street = streetAt(map, map.project(source.coordinates));
            if (street) latest.current.onLocationSelect?.({ ...street, label: source.name });
          }
        }
        return;
      }
      const street = streetAt(map, event.point);
      if (street) latest.current.onLocationSelect?.(street);
      else if (picking) latest.current.onLocationSelect?.({
        coordinates: [event.lngLat.lng, event.lngLat.lat],
        label: `Map point · ${event.lngLat.lat.toFixed(4)}, ${event.lngLat.lng.toFixed(4)}`,
      });
    });
    map.on('mousemove', event => {
      map.getCanvas().style.cursor = latest.current.pickingMode && latest.current.pickingMode !== 'inspect' ? 'crosshair'
        : cameraAt(event.point) || clusterAt(event.point) || layerRef.current?.pickActor(event.point) ? 'pointer' : '';
    });
    const leave = () => { map.getCanvas().style.cursor = ''; };
    map.getCanvas().addEventListener('mouseleave', leave);
    const releaseFollow = (event: { originalEvent?: unknown }) => {
      if (event.originalEvent && latest.current.viewMode === 'follow' && !manuallyReleasedFollow.current) {
        manuallyReleasedFollow.current = true;
        latest.current.onFollowExit();
      }
    };
    map.on('dragstart', releaseFollow);
    map.on('rotatestart', releaseFollow);
    map.on('pitchstart', releaseFollow);
    map.on('wheel', releaseFollow);
    map.on('move', publish);
    map.on('resize', publish);
    map.on('render', () => { renderedFrames += 1; });
    const fpsTimer = window.setInterval(() => {
      const now = performance.now();
      const fps = renderedFrames * 1000 / (now - lastFpsSample);
      map.getContainer().dataset.renderFps = fps.toFixed(1);
      renderedFrames = 0;
      lastFpsSample = now;
    }, 2000);
    map.on('error', event => {
      if (disposed) return;
      if (/webgl|context lost/i.test(event.error?.message ?? '')) {
        setWebglError(true);
        latest.current.onError?.('The 3D view could not initialize. Camera feeds remain available.');
      }
    });
    map.getCanvas().addEventListener('webglcontextlost', () => {
      if (!disposed) setWebglError(true);
    });
    map.getCanvas().addEventListener('webglcontextrestored', () => {
      if (!disposed) { setWebglError(false); map.triggerRepaint(); }
    });

    const fetchStyle = async () => {
      styleRequest?.abort();
      window.clearTimeout(styleTimer);
      const controller = new AbortController();
      styleRequest = controller;
      setBasemapStatus('loading');
      styleTimer = window.setTimeout(() => controller.abort(), 10_000);
      try {
        const response = await fetch(MAP_STYLE_URL, { signal: controller.signal });
        if (!response.ok) throw new Error('Map style unavailable');
        const style = await response.json() as StyleSpecification;
        if (disposed || controller !== styleRequest) return;
        if (!Array.isArray(style.layers) || style.version !== 8) throw new Error('Invalid map style');
        // Custom layers are omitted from style serialization. A diff can retain
        // the fallback's custom layer beneath the new basemap and hide it.
        map.setStyle(gothamStyle(style), { diff: false });
        setBasemapStatus('connected');
      } catch {
        if (!disposed && controller === styleRequest) setBasemapStatus('unavailable');
      } finally {
        if (controller === styleRequest) window.clearTimeout(styleTimer);
      }
    };
    reloadBasemap.current = () => { void fetchStyle(); };
    void fetchStyle();

    const updateLayout = () => {
      if (disposed || layoutFrame) return;
      layoutFrame = requestAnimationFrame(() => {
        layoutFrame = 0;
        if (disposed) return;
        const container = map.getContainer();
        if (container.clientWidth !== lastContainerWidth || container.clientHeight !== lastContainerHeight) {
          lastContainerWidth = container.clientWidth;
          lastContainerHeight = container.clientHeight;
          map.resize();
        }
        const padding = framePadding(map, latest.current);
        const existing = map.getPadding();
        if (Object.entries(padding).some(([edge, value]) => Math.abs((existing[edge as keyof typeof padding] ?? 0) - value) > 0.5)) {
          if (map.isMoving()) {
            // Padding is interpolated during flyTo. Writing it here would stop
            // the perspective transition as the dock's ResizeObserver fires.
            if (!deferredLayout) {
              deferredLayout = true;
              map.once('moveend', () => {
                deferredLayout = false;
                updateLayout();
              });
            }
          } else {
            map.setPadding(padding);
          }
        }
        setControlInsets(map, padding);
        publish();
      });
    };
    resizeObserver = new ResizeObserver(updateLayout);
    const workspace = element.current.closest('.workspace');
    const observeLayout = () => {
      resizeObserver?.disconnect();
      if (element.current) resizeObserver?.observe(element.current);
      workspace?.querySelectorAll('.source-rail,.feed-surface.feed-docked,.playback-bar,.scene-heading,.topbar')
        .forEach(item => resizeObserver?.observe(item));
      updateLayout();
    };
    observeLayout();
    if (workspace) {
      layoutObserver = new MutationObserver(observeLayout);
      layoutObserver.observe(workspace, { childList: true });
    }

    const animate = (now: number) => {
      if (disposed) return;
      const current = latest.current;
      if (!document.hidden && current.viewMode === 'follow' && now > followAfter.current && now - lastFollow > 30 && layerRef.current && map.getLayer(layerRef.current.id)) {
        const delta = Math.min(100, now - lastFollow);
        lastFollow = now;
        const selectedId = current.runtime.optionsRef.current.selectedActorId;
        const actors = current.runtime.frameRef.current.actors;
        const actor = actors.find(item => item.id === selectedId) ?? actors.find(item => item.id === current.scenario.actors.find(definition => definition.role === 'ego')?.id);
        if (actor) {
          const target = geographicPoint(current.scenario.origin, actor.x + Math.cos(actor.heading) * 12, actor.y + Math.sin(actor.heading) * 12);
          const center = map.getCenter();
          const smoothing = reducedMotion() ? 1 : 1 - Math.exp(-delta / 155);
          const targetBearing = toBearing(actor.heading);
          const bearingDelta = ((targetBearing - map.getBearing() + 540) % 360) - 180;
          if (Math.abs(target[0] - center.lng) > 1e-10 || Math.abs(target[1] - center.lat) > 1e-10 || Math.abs(bearingDelta) > 0.0001) {
            map.jumpTo({
              center: [center.lng + (target[0] - center.lng) * smoothing, center.lat + (target[1] - center.lat) * smoothing],
              bearing: map.getBearing() + bearingDelta * smoothing,
            });
          }
        }
      }
      const frame = current.runtime.frameRef.current;
      const options = current.runtime.optionsRef.current;
      const optionKey = `${options.actorsVisible}:${options.routesVisible}:${options.selectedActorId}`;
      const repaintInterval = current.viewMode === 'city' ? 30 : 0;
      if (!document.hidden && now - lastRepaint >= repaintInterval
        && (frame !== lastRenderedFrame || optionKey !== lastRenderedOptions)) {
        map.triggerRepaint();
        lastRenderedFrame = frame;
        lastRenderedOptions = optionKey;
        lastRepaint = now;
      }
      animation = requestAnimationFrame(animate);
    };
    animation = requestAnimationFrame(animate);

    return () => {
      disposed = true;
      cancelAnimationFrame(animation);
      cancelAnimationFrame(layoutFrame);
      styleRequest?.abort();
      window.clearTimeout(styleTimer);
      window.clearInterval(fpsTimer);
      resizeObserver?.disconnect();
      layoutObserver?.disconnect();
      map.getCanvas().removeEventListener('mouseleave', leave);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      publishViewport.current = () => {};
      reloadBasemap.current = () => {};
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource('gotham-cameras') as GeoJSONSource | undefined)?.setData(cameraData(props.cameras));
    (map.getSource('gotham-selected-camera') as GeoJSONSource | undefined)?.setData(cameraData(props.selectedCamera ? [props.selectedCamera] : []));
    publishViewport.current();
  }, [ready, props.cameras, props.selectedCamera]);

  useEffect(() => {
    const map = mapRef.current;
    const scenarioKey = `${props.scenario.id}:${props.scenario.version}`;
    // isStyleLoaded() also becomes false while ordinary map tiles are loading.
    // Our camera layer proves the style installation has completed; style.load
    // installs the latest scenario if a style replacement is actually underway.
    if (!ready || !map || !map.getLayer('camera-glow') || installedScenario.current === scenarioKey) return;
    if (layerRef.current && map.getLayer(layerRef.current.id)) map.removeLayer(layerRef.current.id);
    const layer = createSimulationLayer(props.scenario, props.runtime);
    layerRef.current = layer;
    map.addLayer(layer, map.getLayer('camera-glow') ? 'camera-glow' : undefined);
    installedScenario.current = scenarioKey;
  }, [ready, props.scenario.id, props.scenario.version, props.runtime]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const padding = framePadding(map, props);
    setControlInsets(map, padding);
    const existing = map.getPadding();
    if (!map.isMoving() && Object.entries(padding).some(([edge, value]) => Math.abs((existing[edge as keyof typeof padding] ?? 0) - value) > 0.5)) {
      map.easeTo({ padding, duration: reducedMotion() ? 0 : 300 });
    }
  }, [ready, props.sidebarOpen, props.feedsVisible, props.viewMode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const duration = reducedMotion() ? 0 : 1500;
    const padding = framePadding(map, props);
    followAfter.current = performance.now() + duration + 80;
    if (manuallyReleasedFollow.current && props.viewMode === 'intersection') {
      manuallyReleasedFollow.current = false;
      return;
    }
    manuallyReleasedFollow.current = false;
    if (props.viewMode === 'city') {
      map.fitBounds(NYC_BOUNDS, { padding, absolutePadding: true, duration, pitch: 0, bearing: 0, maxZoom: 11.5, essential: false });
    } else if (props.viewMode === 'intersection') {
      const firstActor = props.runtime.frameRef.current.actors[0];
      map.flyTo({ center: props.scenario.origin, zoom: 18.8, pitch: 61,
        bearing: props.scenario.geometryKind === 'street-route' && firstActor ? toBearing(firstActor.heading) - 35 : -35,
        padding, duration, essential: false });
    } else {
      const frame = props.runtime.frameRef.current;
      const actor = frame.actors.find(item => item.id === props.runtime.optionsRef.current.selectedActorId) ?? frame.actors[0];
      const center = actor ? geographicPoint(props.scenario.origin, actor.x + Math.cos(actor.heading) * 12, actor.y + Math.sin(actor.heading) * 12) : props.scenario.origin;
      map.flyTo({ center, zoom: 20.1, pitch: 73, bearing: actor ? toBearing(actor.heading) : 8, padding, duration, essential: false });
    }
  }, [ready, props.viewMode, props.viewRevision, props.scenario.id]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const layer of map.getStyle().layers) {
      if ('source' in layer && (layer.source === 'gotham-cameras' || layer.source === 'gotham-selected-camera')) {
        map.setLayoutProperty(layer.id, 'visibility', props.viewMode === 'city' ? 'visible' : 'none');
      }
    }
  }, [ready, props.viewMode]);

  const previousCamera = useRef(props.selectedCamera?.id);
  useEffect(() => {
    const map = mapRef.current;
    const camera = props.selectedCamera;
    if (ready && map && camera && previousCamera.current !== camera.id && props.viewMode === 'city') {
      map.flyTo({ center: camera.coordinates, zoom: 16.3, pitch: 25, bearing: 0, padding: framePadding(map, props), duration: reducedMotion() ? 0 : 1050 });
    }
    previousCamera.current = camera?.id;
  }, [ready, props.selectedCamera?.id]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource('gotham-navigation') as GeoJSONSource | undefined)?.setData(navigationData(props));
  }, [ready, props.navigationStart, props.navigationEnd, props.plannedRoute, props.focusLocation]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    for (const id of ['navigation-route', 'navigation-route-outline']) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', routesVisible && props.viewMode === 'city' ? 'visible' : 'none');
    }
  }, [ready, routesVisible, props.viewMode]);

  useEffect(() => {
    const map = mapRef.current;
    const point = props.focusLocation;
    if (!ready || !map || !point) return;
    map.flyTo({ center: point.coordinates, zoom: Math.max(map.getZoom(), 16.5), pitch: 25,
      padding: framePadding(map, props), duration: reducedMotion() ? 0 : 650 });
  }, [ready, props.focusRevision]);

  useEffect(() => {
    const map = mapRef.current;
    const coordinates = props.plannedRoute?.coordinates;
    if (!ready || !map || !coordinates?.length || props.viewMode !== 'city') return;
    const bounds = new maplibregl.LngLatBounds(coordinates[0], coordinates[0]);
    for (const point of coordinates) bounds.extend(point);
    map.fitBounds(bounds, { padding: framePadding(map, props), absolutePadding: true, maxZoom: 17, pitch: 0, bearing: 0,
      duration: reducedMotion() ? 0 : 750 });
  }, [ready, props.plannedRoute]);

  return (
    <div className={`city-map-root${props.feedsVisible ? ' city-map-root--docked' : ''}`}>
      <div ref={element} className="city-map-canvas" role="region" aria-label="Interactive 3D New York City map. Use the camera list and view controls for keyboard navigation." />
      {!ready && !webglError && <div className="city-map-loading" role="status"><span />Building the city view</div>}
      {basemapStatus !== 'connected' && ready && !webglError && (
        <div className="map-context-status" role="status">
          <span className={`map-context-status__dot ${basemapStatus === 'loading' ? 'is-loading' : ''}`} />
          {basemapStatus === 'loading' ? 'Loading city context' : 'City map offline · local scene available'}
          {basemapStatus === 'unavailable' && <button type="button" onClick={() => reloadBasemap.current()}>Retry</button>}
        </div>
      )}
      {webglError && <div className="map-error" role="status"><strong>The 3D view is unavailable</strong><p>Camera feeds and playback controls remain accessible. Try a browser with WebGL enabled.</p></div>}
    </div>
  );
}
