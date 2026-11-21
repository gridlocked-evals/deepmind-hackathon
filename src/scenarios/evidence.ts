import { createPath, routePosition } from '../agent/physics';
import type { ExtractionReport } from '../evidence/api';
import { preset } from './events';
import type { ScenarioPlan } from './events';
import type { ActorDefinition, CameraSource, LngLat, ScenarioBundle } from '../types';

/** What one accepted Hydrogen extraction contributes to a simulator drive at that camera. */
export interface EvidenceConditions {
  cameraId: string;
  coordinates: LngLat;
  extractionId: string;
  vehicles: number;
  obstruction: boolean;
}

const MAX_VEHICLES = 8;
const NEAR_CAMERA_M = 400;
// shortcut: counts are image-space, so spacing and speed are assumed; replace once calibrated geometry (#16) exists.
const ASSUMED_SPEED_MPS = 6;
const FIRST_GAP_M = 30;
const SPACING_M = 20;

export function evidenceConditions(report: ExtractionReport, camera: CameraSource): EvidenceConditions {
  if (report.sourceId !== camera.id) throw new Error('This extraction belongs to a different camera.');
  const accepted = report.facts.filter(fact => fact.disposition === 'accepted');
  // Counts describe one frame each, so the largest lower bound is the most vehicles seen at once.
  const seen = accepted.filter(fact => fact.kind === 'count' && fact.semanticClass === 'vehicle').map(fact => fact.count?.lower ?? 0);
  return { cameraId: camera.id, coordinates: camera.coordinates, extractionId: report.id,
    vehicles: Math.min(MAX_VEHICLES, Math.max(0, ...seen)), obstruction: accepted.some(fact => fact.kind === 'obstruction') };
}

/** Evidence only applies to a drivable street scenario tagged with, and starting near, the same camera. */
export function evidenceApplies(scenario: ScenarioBundle, conditions: EvidenceConditions | null): conditions is EvidenceConditions {
  if (!conditions || scenario.cameraId !== conditions.cameraId || scenario.geometry.route.length < 2
    || (scenario.motionSource !== 'driving-route' && scenario.motionSource !== 'street-preview')) return false;
  const [lng, lat] = scenario.origin, [cameraLng, cameraLat] = conditions.coordinates;
  const metres = Math.hypot((lng - cameraLng) * 111_320 * Math.cos(lat * Math.PI / 180), (lat - cameraLat) * 111_320);
  return metres <= NEAR_CAMERA_M;
}

/** An observed obstruction becomes a stalled-vehicle hazard, but only on drives the evidence applies to. */
export function withEvidenceHazard(scenario: ScenarioBundle, conditions: EvidenceConditions | null, plan: ScenarioPlan): ScenarioPlan {
  return evidenceApplies(scenario, conditions) && conditions.obstruction ? preset('stalled-vehicle') : plan;
}

export function withEvidenceTraffic(scenario: ScenarioBundle, conditions: EvidenceConditions | null): ScenarioBundle {
  if (!evidenceApplies(scenario, conditions) || !conditions.vehicles) return scenario;
  const path = createPath(scenario.geometry.route);
  const routeId = scenario.actors.find(actor => actor.role === 'ego')?.routeId ?? 'street-route';
  const cars: { definition: ActorDefinition; start: number; stop: number }[] = [];
  for (let index = 0; index < conditions.vehicles; index++) {
    const start = FIRST_GAP_M + SPACING_M * index;
    if (start > path.length - 10) break;
    cars.push({ start, stop: Math.max(start, path.length - 6 - (conditions.vehicles - 1 - index) * 7), definition: {
      id: `evidence-car-${index}`, kind: 'car', role: 'traffic', routeId, length: 4.55, width: 1.85, height: 1.55, color: '#9fb4c7',
      label: 'Camera-counted vehicle (position and motion assumed)' } });
  }
  if (!cars.length) return scenario;
  if (scenario.actors.length + cars.length > 64) throw new Error('This scene exceeds the renderer actor limit.');
  return {
    ...scenario,
    version: `${scenario.version}-evidence-${conditions.extractionId}`,
    label: `${scenario.label} · traffic count from camera evidence`,
    actors: [...scenario.actors, ...cars.map(car => car.definition)],
    frames: scenario.frames.map(frame => ({ ...frame, actors: [...frame.actors, ...cars.map(car => {
      const progress = Math.min(car.stop, car.start + ASSUMED_SPEED_MPS * frame.time), pose = routePosition(path, progress);
      return { id: car.definition.id, x: pose.x, y: pose.y, heading: pose.heading, speed: progress < car.stop ? ASSUMED_SPEED_MPS : 0, braking: false };
    })] })),
  };
}
