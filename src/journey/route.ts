import type { NavigationPoint } from '../navigation/types';
import type { DriverSimulation } from '../agent/physics';

export const NYC_STOPS: NavigationPoint[] = [
  { label: 'Tribeca · Hudson Street', coordinates: [-74.0085, 40.7218] },
  { label: 'West Village · Seventh Avenue', coordinates: [-74.0026, 40.7342] },
  { label: 'Chelsea · Eighth Avenue', coordinates: [-73.9982, 40.7440] },
  { label: 'Midtown · Broadway', coordinates: [-73.9876, 40.7556] },
  { label: 'Flatiron · Fifth Avenue', coordinates: [-73.9897, 40.7412] },
  { label: 'East Village · First Avenue', coordinates: [-73.9849, 40.7294] },
  { label: 'Lower East Side · Allen Street', coordinates: [-73.9905, 40.7195] },
];
export const STUCK_SECONDS = 25;
const MIN_PROGRESS = 2;

export class ProgressWatch {
  private time: number;
  private progress: number;
  constructor(state: Pick<DriverSimulation, 'time' | 'progress'>) { this.time = state.time; this.progress = state.progress; }
  stuck(state: Pick<DriverSimulation, 'time' | 'progress'>) {
    if (state.progress - this.progress >= MIN_PROGRESS) { this.time = state.time; this.progress = state.progress; }
    return state.time - this.time >= STUCK_SECONDS;
  }
}

export function randomStop(exclude: NavigationPoint | null, random = Math.random): NavigationPoint {
  const choices = NYC_STOPS.filter(point => !exclude || Math.hypot(point.coordinates[0] - exclude.coordinates[0], point.coordinates[1] - exclude.coordinates[1]) > .003);
  return choices[Math.min(choices.length - 1, Math.max(0, Math.floor(random() * choices.length)))];
}
