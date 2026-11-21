import Ajv2020 from 'ajv/dist/2020';
import schema from './schema.json';
import type {
  CaptureContext, EvidenceRef, ModelTrace, ObservationBatch, StreetEdit,
  ReferenceImage, GateDecision, WorldSession, NavigationEvent, EvaluationResult, VerticalSlice,
} from './types';

export type * from './types';

export interface ContractMap {
  CaptureContext: CaptureContext;
  ModelTrace: ModelTrace;
  ObservationBatch: ObservationBatch;
  StreetEdit: StreetEdit;
  ReferenceImage: ReferenceImage;
  GateDecision: GateDecision;
  WorldSession: WorldSession;
  NavigationEvent: NavigationEvent;
  EvaluationResult: EvaluationResult;
  VerticalSlice: VerticalSlice;
}
type Artifact = ObservationBatch | StreetEdit | ReferenceImage | GateDecision | WorldSession | NavigationEvent | EvaluationResult;
type ObjectValue = Record<string, unknown>;
const ajv = new Ajv2020({ allErrors: true, strict: true });
ajv.addSchema(schema);
const validators = new Map<string, ReturnType<typeof ajv.compile>>();

export class ContractValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ContractValidationError';
  }
}

function requireCondition(condition: unknown, message: string): asserts condition {
  if (!condition) throw new ContractValidationError(message);
}

function walk(value: unknown, visit: (object: ObjectValue) => void): void {
  if (Array.isArray(value)) value.forEach((item) => walk(item, visit));
  else if (value !== null && typeof value === 'object') {
    const object = value as ObjectValue;
    visit(object);
    Object.values(object).forEach((item) => walk(item, visit));
  }
}

function validateCapture(capture: CaptureContext): void {
  const { allowedInterval: range } = capture;
  requireCondition(range.endSeconds <= capture.durationSeconds, 'Allowed evidence interval exceeds source duration');
  requireCondition(capture.modality !== 'snapshot' || capture.durationSeconds === 0, 'A snapshot has zero temporal duration');
  requireCondition(capture.modality !== 'video' || (capture.durationSeconds > 0 && range.endSeconds > range.startSeconds), 'Video requires a positive permitted interval');
  requireCondition(capture.sourceClock !== 'unknown' || (capture.sourceCapturedAt === null && capture.clockUncertaintySeconds === null), 'Unknown source clock cannot claim exposure time or measured clock uncertainty');
  requireCondition(capture.sourceClock === 'unknown' || capture.sourceCapturedAt !== null, 'Known source clock requires a source timestamp');
}

function model(artifact: Artifact, id: ModelTrace['modelId']): void {
  requireCondition(artifact.trace?.modelId === id && artifact.trace.outcome === 'success', `Artifact requires a successful ${id} trace`);
}

function unique(values: unknown[], message: string): void {
  requireCondition(new Set(values).size === values.length, message);
}

function validateArtifact(artifact: Artifact): void {
  const capture = artifact.capture;
  walk(artifact, (object) => {
    if ('timeSeconds' in object && 'originalMediaSha256' in object) {
      const ref = object as unknown as EvidenceRef;
      requireCondition(ref.captureId === capture.captureId && ref.originalMediaSha256 === capture.originalMediaSha256, 'Evidence refers to another capture or original media hash');
      const inside = capture.modality === 'video'
        ? ref.timeSeconds >= capture.allowedInterval.startSeconds && ref.timeSeconds < capture.allowedInterval.endSeconds
        : ref.timeSeconds === 0;
      requireCondition(inside, 'Evidence time lies outside permitted source interval');
      requireCondition(capture.modality !== 'snapshot' || ref.frameIndex === null || ref.frameIndex === 0, 'Snapshot evidence cannot reference subsequent frames');
    }
  });
  switch (artifact.kind) {
    case 'ObservationBatch':
      model(artifact, 'gemini-4-hydrogen');
      unique(artifact.observations.map((item) => item.id), 'Observation IDs must be unique');
      requireCondition(capture.modality !== 'snapshot' || !artifact.observations.some((item) => item.category === 'event'), 'Snapshot observations cannot claim temporal events');
      break;
    case 'StreetEdit':
      model(artifact, 'gemini-4-carbon');
      unique(artifact.regions.map((item) => item.id), 'Edited region IDs must be unique');
      requireCondition(artifact.status !== 'accepted' || (artifact.regions.length && artifact.nanoBananaPrompt && artifact.worldPrompt), 'Accepted edits require regions, image instructions, and a world prompt');
      break;
    case 'ReferenceImage':
      model(artifact, 'gemini-nano-banana-2.1');
      requireCondition(artifact.width * 9 === artifact.height * 16, 'Reference image must have a 16:9 aspect ratio');
      requireCondition(artifact.provenance === 'generated', 'Reference image must retain generated provenance');
      break;
    case 'GateDecision': {
      model(artifact, 'gemini-decision-1');
      const results = artifact.checks.map((item) => item.result);
      const verdict = results.includes('fail') ? 'reject' : results.includes('unknown') ? 'unknown' : 'accept';
      const action = { accept: 'allow_world_creation', reject: 'block', unknown: 'review' }[verdict];
      requireCondition(artifact.verdict === verdict && artifact.action === action, 'Gate verdict/action contradict its checks; unknown checks cannot authorize a world');
      break;
    }
    case 'WorldSession':
      requireCondition(artifact.trace === null || artifact.trace.modelId === 'genie-4', 'World session trace must identify Genie');
      requireCondition(!['active', 'stopping', 'completed'].includes(artifact.state) || artifact.interactionId !== null, 'Connected session requires its provider interaction ID');
      requireCondition(!['completed', 'failed'].includes(artifact.state) || artifact.terminationReason, 'Terminal session requires an explicit termination reason');
      break;
    case 'NavigationEvent':
      if (artifact.controller === 'hydrogen') model(artifact, 'gemini-4-hydrogen');
      requireCondition(!['release_all', 'terminate'].includes(artifact.action) || artifact.holdMilliseconds === 0, 'Release/termination commands cannot hold movement keys');
      break;
    case 'EvaluationResult':
      requireCondition(artifact.unknownSeconds <= artifact.evaluatedSeconds, 'Unknown coverage exceeds evaluated duration');
      unique(artifact.attemptIds, 'Evaluation attempt IDs must be unique');
      break;
  }
}

// JSON Schema expresses field shapes; these checks express relationships between fields.
function validateSemantics(value: unknown): void {
  walk(value, (object) => {
    for (const [key, item] of Object.entries(object)) {
      if (key.endsWith('At') && typeof item === 'string') {
        const parsed = new Date(item);
        requireCondition(Number.isFinite(parsed.getTime()), 'Invalid UTC timestamp');
        // Date normalizes February 30; reject invalid calendar days as Python does.
        requireCondition(parsed.toISOString().slice(0, 19) === item.slice(0, 19), 'Invalid UTC calendar date');
      }
    }
    if ('x' in object && 'width' in object) {
      requireCondition(Number(object.x) + Number(object.width) <= 1 && Number(object.y) + Number(object.height) <= 1, 'Image region extends outside normalized image bounds');
    }
    if ('startSeconds' in object) requireCondition(Number(object.endSeconds) >= Number(object.startSeconds), 'Time interval ends before it starts');
    if ('sourceClock' in object) validateCapture(object as unknown as CaptureContext);
    if ('startedAt' in object) requireCondition(Date.parse(String(object.completedAt)) >= Date.parse(String(object.startedAt)), 'Model trace completed before it started');
    if ('unknownReason' in object && object.value === null) requireCondition(object.unknownReason, 'Unknown observations/metrics require an explanation');
    if ('minimumConfidence' in object) {
      const result = object.probability === null || object.confidence === null || Number(object.confidence) < Number(object.minimumConfidence)
        ? 'unknown' : Number(object.probability) >= Number(object.threshold) ? 'pass' : 'fail';
      requireCondition(object.result === result, 'Gate check result contradicts probability/confidence thresholds');
    }
    if (Array.isArray(object.permissions)) unique(object.permissions.map((permission) => (permission as ObjectValue).actor), 'Each actor has exactly one permission per region');
    if ('kind' in object && 'capture' in object) validateArtifact(object as unknown as Artifact);
  });
}

function validateLinks(slice: VerticalSlice): void {
  const artifacts: Artifact[] = [slice.observations, slice.edit, slice.reference, slice.gate, slice.session, ...slice.navigation, slice.evaluation];
  // Input property order is irrelevant; compare capture fields in canonical schema order.
  const captureKey = (capture: CaptureContext) => [capture.captureId, capture.sourceId, capture.originalMediaSha256, capture.modality, capture.durationSeconds, capture.allowedInterval.startSeconds, capture.allowedInterval.endSeconds, capture.receivedAt, capture.sourceCapturedAt, capture.sourceClock, capture.clockUncertaintySeconds].join('|');
  requireCondition(artifacts.every((item) => captureKey(item.capture) === captureKey(slice.observations.capture)), 'Vertical slice artifacts must retain the same immutable capture context');
  unique(artifacts.map((item) => item.id), 'Artifact IDs must be unique');
  const links = [[slice.edit.observationBatchId, slice.observations.id], [slice.reference.streetEditId, slice.edit.id], [slice.gate.referenceImageId, slice.reference.id], [slice.session.referenceImageId, slice.reference.id], [slice.session.gateDecisionId, slice.gate.id], ...slice.navigation.map((item) => [item.worldSessionId, slice.session.id])];
  if (slice.evaluation.worldSessionId !== null) links.push([slice.evaluation.worldSessionId, slice.session.id]);
  requireCondition(links.every(([actual, expected]) => actual === expected), 'Artifact references do not match the vertical slice');
  requireCondition(slice.edit.status === 'accepted' && slice.gate.verdict === 'accept', 'World creation requires an accepted edit and image gate');
  requireCondition(slice.edit.preservedObservationIds.every((id) => slice.observations.observations.some((item) => item.id === id)), 'Preserved observations must exist in the observation batch');
  unique(slice.navigation.map((item) => item.sequence), 'Navigation sequence numbers must be unique');
  requireCondition(slice.navigation.every((item) => item.sessionTimeSeconds <= slice.session.maximumDurationSeconds), 'Navigation event exceeds the world session time budget');
}

/** Parse untrusted API JSON before using it; throws a readable validation error. */
export function parseContract<K extends keyof ContractMap>(name: K, value: unknown): ContractMap[K] {
  let validate = validators.get(name);
  if (!validate) {
    validate = ajv.compile({ $ref: name === 'VerticalSlice' ? schema.$id : `${schema.$id}#/$defs/${name}` });
    validators.set(name, validate);
  }
  if (!validate(value)) throw new ContractValidationError(ajv.errorsText(validate.errors, { separator: '; ' }));
  validateSemantics(value);
  if (name === 'VerticalSlice') validateLinks(value as VerticalSlice);
  return value as ContractMap[K];
}
