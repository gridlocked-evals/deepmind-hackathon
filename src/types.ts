import type { MutableRefObject } from 'react';

export type ViewMode = 'city' | 'intersection' | 'follow';
export type Vec2 = [number, number];
export type LngLat = [number, number];
export interface CameraSource {
  id: string;
  name: string;
  kind: 'hls' | 'snapshot';
  coordinates: LngLat;
  provider: string;
  borough?: string;
  sourceUrl: string;
  playbackUrl: string;
  thumbnailUrl?: string;
  description?: string;
  resolution?: string;
  capturedAt: string | null;
  fetchedAt?: string | null;
  availability: 'unverified' | 'connected' | 'stale' | 'unavailable';
}
export interface LaneDefinition {
  id: string;
  points: Vec2[];
  width: number;
  direction: 'north' | 'south' | 'east' | 'west';
}
export interface ActorDefinition {
  id: string;
  kind: 'car' | 'van' | 'bus';
  label: string;
  role: 'ego' | 'traffic';
  length: number;
  width: number;
  height: number;
  color: string;
  routeId: string;
}
export interface ActorState {
  id: string;
  x: number;
  y: number;
  heading: number;
  speed: number;
  braking: boolean;
}
export interface SimulationFrame {
  time: number;
  actors: ActorState[];
  signal: 'green' | 'amber' | 'red';
}
export interface ScenarioBundle {
  id: string;
  version: string;
  label: string;
  provenance: 'demo' | 'backend';
  seed: number;
  cameraId: string;
  origin: LngLat;
  duration: number;
  playbackMode?: 'loop' | 'once';
  geometryKind?: 'authored' | 'street-route';
  motionSource?: 'authored' | 'street-preview' | 'driving-route' | 'location-preview';
  geometry: {
    roads: Vec2[][];
    sidewalks: Vec2[][];
    crosswalks: Vec2[][];
    lanes: LaneDefinition[];
    route: Vec2[];
  };
  actors: ActorDefinition[];
  frames: SimulationFrame[];
}
export interface ScenarioProvider {
  readonly source: 'demo' | 'backend';
  loadScenario(scenarioId: string, signal?: AbortSignal): Promise<ScenarioBundle>;
}
export interface RenderOptions {
  actorsVisible: boolean;
  routesVisible: boolean;
  selectedActorId: string;
}
export interface SceneRuntime {
  frameRef: MutableRefObject<SimulationFrame>;
  optionsRef: MutableRefObject<RenderOptions>;
}
export interface MapViewport {
  width: number;
  height: number;
  cameraPoint: { x: number; y: number } | null;
}
