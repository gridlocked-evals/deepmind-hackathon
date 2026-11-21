import type { MutableRefObject } from 'react';
import type { SimulationFrame } from '../types';
import type { RunTrace } from '../scenarios/trace';
import type { DriverTrace } from './trace';

export const MAX_DRIVER_STEPS = 60;
export const DECISION_INTERVAL_SECONDS = 1;
export const MAX_COMMAND_AGE_SECONDS = 3;
export const MAX_DRIVE_SECONDS = 60;
export const SPEED_LIMIT_MPS = 11.176;

/** Local controller guard that changed applied motion; route/turn shaping is not a guard. */
export type GuardReason = 'leader_guard' | 'red_line_guard' | 'none';

export interface DriverObservation {
  scenario_id: string;
  simulation_time: number;
  speed_mps: number;
  speed_limit_mps: number;
  remaining_m: number;
  signal: 'green' | 'amber' | 'red' | 'unknown';
  stop_distance_m: number | null;
  lead_gap_m: number | null;
  lead_speed_mps: number | null;
  upcoming_turn_degrees: number;
  route_label: string;
}

export interface DriverDecision {
  action: 'accelerate' | 'cruise' | 'brake' | 'stop';
  target_speed_mps: number;
  reason: string;
  confidence: number;
}

export interface DriverResponse {
  run_id: string;
  step: number;
  model_id: string;
  request_id: string;
  decision: DriverDecision;
  max_steps: number;
}

export interface DecisionEntry extends DriverResponse {
  simulationTime: number;
}

export type DriverStatus = 'idle' | 'thinking' | 'driving' | 'stopped' | 'complete' | 'error';
export interface DriverAvailability {
  state: 'checking' | 'available' | 'unavailable';
  modelId: string | null;
  reason: string | null;
}

export interface GeminiDriver {
  frameRef: MutableRefObject<SimulationFrame>;
  time: number;
  status: DriverStatus;
  active: boolean;
  hasRun: boolean;
  speedMps: number;
  remainingM: number;
  decisionLog: DecisionEntry[];
  error: string | null;
  modelAvailability: DriverAvailability;
  canStart: boolean;
  unavailableReason: string | null;
  completionReason: 'route-complete' | 'decision-limit' | 'time-limit' | null;
  start: (mode?: 'baseline' | 'event') => void;
  stop: () => void;
  reset: () => void;
  /** The finished run's trace, or null while active / after reset. */
  exportTrace: () => DriverTrace | null;
  refreshAvailability: () => void;
  inject: (id: string) => void;
  trace: RunTrace | null;
  traces: RunTrace[];
  replaying: boolean;
}
