import type { MutableRefObject } from 'react';
import type { SimulationFrame } from '../types';

export const MAX_DRIVER_STEPS = 12;
export const SECONDS_PER_DECISION = 3;
export const SPEED_LIMIT_MPS = 11.176;

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
  completionReason: 'route-complete' | 'decision-limit' | null;
  start: () => void;
  stop: () => void;
  reset: () => void;
  refreshAvailability: () => void;
}
