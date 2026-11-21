import type { LngLat } from '../types';

export interface NavigationPoint {
  coordinates: LngLat;
  label: string;
  /** A contiguous street line from the basemap; direction is not a routing instruction. */
  streetPath?: LngLat[];
}

export interface PlannedRoute {
  coordinates: LngLat[];
  distanceMeters: number;
  /** Provider travel estimate, not measured traffic or the simulation's runtime. */
  durationSeconds: number;
  source: string;
  steps: { label: string; distanceMeters: number }[];
}
