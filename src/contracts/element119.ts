/**
 * Element 119 shared contracts, schema version `element119.v1`.
 *
 * Mirrors backend/contracts.py (the source of truth; JSON Schema is served at
 * GET /api/contracts/element119). `validateVerticalSlice` performs the same
 * runtime checks on untrusted API payloads so the UI never renders an episode
 * the backend would reject. See docs/CONTRACTS.md for ownership and routes.
 */

export const SCHEMA_VERSION = 'element119.v1';
export const MAX_EVIDENCE_SECONDS = 120;

export type Sha256 = `sha256:${string}`;
export type IsoDateTime = string;
export type Provenance = 'observed' | 'derived' | 'assumed' | 'inserted' | 'generated';
export type Service = 'hydrogen' | 'carbon' | 'nano_banana' | 'decisions' | 'genie';
export const SEMANTIC_CLASSES = [
  'travel_lane', 'bus_lane', 'bike_lane', 'parking_lane', 'sidewalk', 'crosswalk',
  'plaza', 'median', 'planter', 'signal', 'signage', 'vehicle', 'pedestrian',
  'cyclist', 'building_facade', 'other',
] as const;
export type SemanticClass = (typeof SEMANTIC_CLASSES)[number];

export interface Unknown { field: string; reason: string }

export interface ModelCall {
  id: string;
  service: Service;
  modelId: string;
  promptId: string;
  configId: string;
  requestId: string | null;
  inputHashes: Sha256[];
  requestedAt: IsoDateTime;
  completedAt: IsoDateTime | null;
  latencyMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  costUsd: number | null;
  outcome: 'ok' | 'timeout' | 'quota' | 'invalid_output' | 'error' | 'blocked';
}

/** Normalized image coordinates: x right, y down, both in [0, 1]. */
export type ImageRegion =
  | { kind: 'box'; x: number; y: number; w: number; h: number }
  | { kind: 'polygon'; points: [number, number][] };

/** Seconds from the first frame of the captured media. */
export interface MediaTimeRange { startS: number; endS: number }

export interface Capture {
  id: string;
  cameraId: string;
  sourceKind: 'hls' | 'snapshot';
  mediaSha256: Sha256;
  mediaType: string;
  durationS: number;
  frameCount: number;
  width: number;
  height: number;
  /** When we retrieved the media; never the camera's exposure time. */
  retrievalStartedAt: IsoDateTime;
  retrievalEndedAt: IsoDateTime;
  captureClock: 'unknown' | 'source_reported';
  provenance: 'observed';
  unknowns: Unknown[];
}

export interface Anchor {
  id: string;
  label: string;
  semanticClass: SemanticClass;
  region: ImageRegion;
  provenance: 'observed' | 'derived';
}

export interface Observation {
  id: string;
  kind: 'actor' | 'signal_state' | 'lane_use' | 'queue' | 'movement' | 'obstruction' | 'scene';
  semanticClass: SemanticClass;
  label: string;
  region: ImageRegion;
  time: MediaTimeRange;
  confidence: number;
  provenance: 'observed' | 'derived';
}

export interface ObservationBatch {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  captureId: string;
  modelCall: ModelCall;
  motionSupported: boolean;
  anchors: Anchor[];
  observations: Observation[];
  unknowns: Unknown[];
}

export interface EditOperation {
  id: string;
  op: 'add' | 'remove' | 'modify' | 'restyle';
  target: SemanticClass;
  region: ImageRegion;
  anchorIds: string[];
  description: string;
  provenance: 'inserted';
}

export interface StreetEdit {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  observationBatchId: string;
  mayorRequest: string;
  modelCall: ModelCall;
  status: 'compiled' | 'rejected' | 'needs_clarification';
  permissions: { allowed: SemanticClass[]; forbidden: SemanticClass[] };
  operations: EditOperation[];
  rejectionReason: string | null;
  unknowns: Unknown[];
}

export interface ReferenceImage {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  captureId: string;
  streetEditId: string;
  style: string;
  attempt: number;
  modelCall: ModelCall;
  imageSha256: Sha256;
  width: number;
  height: number;
  provenance: 'generated';
}

export type GateOutcome = 'accept' | 'reject' | 'unknown';
export type GateEffect = 'allow_world_creation' | 'block_world_creation' | 'continue' | 'terminate_session' | 'flag_unknown';
export const GATE_EFFECTS: Record<'reference_image' | 'navigation_window', Record<GateOutcome, GateEffect>> = {
  reference_image: { accept: 'allow_world_creation', reject: 'block_world_creation', unknown: 'block_world_creation' },
  navigation_window: { accept: 'continue', reject: 'terminate_session', unknown: 'flag_unknown' },
};

export interface GateDecision {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  subject: { kind: 'reference_image' | 'navigation_window'; id: string; sessionTime: [number, number] | null };
  modelCall: ModelCall;
  checks: { question: string; answer: 'pass' | 'fail' | 'unknown'; score: number | null; rationale: string }[];
  outcome: GateOutcome;
  effect: GateEffect;
}

/** A stochastic Genie session. It has no seed/branch and cannot be replayed exactly. */
export interface WorldSession {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  genieSessionId: string | null;
  referenceImageId: string;
  gateDecisionId: string;
  modelCall: ModelCall;
  status: 'requested' | 'streaming' | 'terminated' | 'failed';
  startedAt: IsoDateTime | null;
  terminatedAt: IsoDateTime | null;
  terminationReason: 'user' | 'gate' | 'budget' | 'timeout' | 'completed' | 'error' | null;
  budget: { maxSeconds: number; maxUsd: number; spentSeconds: number; spentUsd: number };
  deterministicReplay: false;
}

export interface NavigationEvent {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  worldSessionId: string;
  sequence: number;
  /** Seconds since the Genie session started streaming. */
  sessionTimeS: number;
  driver: 'human' | 'hydrogen';
  action: 'forward' | 'back' | 'strafe_left' | 'strafe_right' | 'turn_left' | 'turn_right' | 'stop' | 'terminate';
  durationS: number;
  frameSha256: Sha256 | null;
  modelCall: ModelCall | null;
  rationale: string | null;
}

export interface EvaluationResult {
  schemaVersion: typeof SCHEMA_VERSION;
  id: string;
  inputSetId: string;
  inputSetSha256: Sha256;
  condition: string;
  isBaseline: boolean;
  captureIds: string[];
  worldSessionIds: string[];
  attempts: number;
  succeeded: number;
  failed: number;
  unknown: number;
  metrics: { name: string; value: number | null; unit: string; n: number; unknownCount: number }[];
  provenance: 'derived';
  notes: string | null;
}

export interface VerticalSlice {
  schemaVersion: typeof SCHEMA_VERSION;
  capture: Capture;
  observationBatch: ObservationBatch;
  streetEdit: StreetEdit;
  referenceImage: ReferenceImage;
  referenceGate: GateDecision;
  worldSession: WorldSession;
  navigationEvents: NavigationEvent[];
  navigationGates: GateDecision[];
  evaluation: EvaluationResult | null;
}

// ---------------------------------------------------------------- validation

export type ValidationResult<T> = { ok: true; value: T } | { ok: false; errors: string[] };

const idPattern = (prefix: string) => new RegExp(`^${prefix}_[A-Za-z0-9-]{4,64}$`);
const SHA = /^sha256:[0-9a-f]{64}$/;
const CAMERA = /^[A-Za-z0-9-]{1,100}$/;
const FORBIDDEN_SESSION_KEYS = ['seed', 'branchId', 'branch', 'replaySeed'];

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === 'object' && v !== null && !Array.isArray(v);
const inUnit = (n: unknown) => typeof n === 'number' && n >= 0 && n <= 1;

class Checker {
  errors: string[] = [];
  check(condition: boolean, message: string) { if (!condition) this.errors.push(message); return condition; }
  id(value: unknown, prefix: string, at: string) { this.check(typeof value === 'string' && idPattern(prefix).test(value), `${at}: malformed ${prefix}_ id`); }
  hash(value: unknown, at: string) { this.check(typeof value === 'string' && SHA.test(value), `${at}: malformed sha256 hash`); }
  call(value: unknown, service: Service, at: string) {
    if (!this.check(isRec(value), `${at}: missing model call`)) return;
    const c = value as Rec;
    this.id(c.id, 'call', `${at}.id`);
    this.check(c.service === service, `${at}: expected a ${service} model call`);
    for (const key of ['modelId', 'promptId', 'configId']) this.check(typeof c[key] === 'string' && c[key] !== '', `${at}.${key}: required`);
    const hashes = c.inputHashes;
    if (this.check(Array.isArray(hashes) && hashes.length > 0, `${at}.inputHashes: required`)) (hashes as unknown[]).forEach((h, i) => this.hash(h, `${at}.inputHashes[${i}]`));
  }
  region(value: unknown, at: string) {
    if (!this.check(isRec(value), `${at}: missing region`)) return;
    const r = value as Rec;
    if (r.kind === 'box') {
      const { x, y, w, h } = r as Record<string, number>;
      if (!this.check([x, y, w, h].every(inUnit) && w > 0 && h > 0, `${at}: box values must be normalized and positive`)) return;
      this.check(x + w <= 1 + 1e-9 && y + h <= 1 + 1e-9, `${at}: box region extends outside the image`);
    } else if (r.kind === 'polygon') {
      const pts = r.points;
      if (!this.check(Array.isArray(pts) && pts.length >= 3 && pts.length <= 64 && pts.every((p) => Array.isArray(p) && p.length === 2 && p.every(inUnit)), `${at}: polygon needs 3-64 normalized points`)) return;
      const p = pts as [number, number][];
      const area = p.reduce((sum, [x1, y1], i) => { const [x2, y2] = p[(i + 1) % p.length]; return sum + x1 * y2 - x2 * y1; }, 0);
      this.check(Math.abs(area) >= 1e-9, `${at}: polygon region has zero area`);
    } else this.check(false, `${at}: unknown region kind`);
  }
  gate(g: GateDecision, at: string) {
    this.id(g.id, 'gate', `${at}.id`);
    this.call(g.modelCall, 'decisions', `${at}.modelCall`);
    if (!this.check(Array.isArray(g.checks) && g.checks.length > 0, `${at}.checks: required`)) return;
    const answers = new Set(g.checks.map((c) => c.answer));
    const expected: GateOutcome = answers.has('fail') ? 'reject' : answers.has('unknown') ? 'unknown' : 'accept';
    this.check(g.outcome === expected, `${at}: outcome ${g.outcome} does not follow from checks (expected ${expected})`);
    this.check(GATE_EFFECTS[g.subject.kind]?.[g.outcome] === g.effect, `${at}: effect ${g.effect} is inconsistent with a ${g.outcome} ${g.subject.kind} gate`);
  }
}

/** Runtime validation of an untrusted vertical-slice payload. */
export function validateVerticalSlice(input: unknown): ValidationResult<VerticalSlice> {
  const c = new Checker();
  if (!isRec(input) || input.schemaVersion !== SCHEMA_VERSION) return { ok: false, errors: [`schemaVersion must be ${SCHEMA_VERSION}`] };
  const s = input as unknown as VerticalSlice;
  for (const key of ['capture', 'observationBatch', 'streetEdit', 'referenceImage', 'referenceGate', 'worldSession'] as const) {
    if (!isRec(s[key])) return { ok: false, errors: [`${key}: required`] };
  }
  const { capture: cap, observationBatch: obs, streetEdit: edit, referenceImage: ref, referenceGate: gate, worldSession: world } = s;
  const navigationEvents = s.navigationEvents ?? [];
  const navigationGates = s.navigationGates ?? [];

  c.id(cap.id, 'cap', 'capture.id');
  c.check(typeof cap.cameraId === 'string' && CAMERA.test(cap.cameraId), 'capture.cameraId: malformed camera id');
  c.hash(cap.mediaSha256, 'capture.mediaSha256');
  c.check(typeof cap.durationS === 'number' && cap.durationS >= 0 && cap.durationS <= MAX_EVIDENCE_SECONDS, `capture.durationS: must be within 0-${MAX_EVIDENCE_SECONDS} s`);
  c.check(cap.sourceKind !== 'snapshot' || cap.durationS === 0, 'capture: snapshot captures have zero media duration');
  c.check(Date.parse(cap.retrievalEndedAt) >= Date.parse(cap.retrievalStartedAt), 'capture: retrieval ends before it starts');

  c.id(obs.id, 'obs', 'observationBatch.id');
  c.check(obs.captureId === cap.id, 'observationBatch: must reference the capture');
  c.call(obs.modelCall, 'hydrogen', 'observationBatch.modelCall');
  c.check(obs.modelCall?.inputHashes?.includes(cap.mediaSha256), 'observationBatch: Hydrogen observations must hash the original capture media');
  const anchorIds = new Set<string>();
  obs.anchors.forEach((a, i) => { c.id(a.id, 'anc', `anchors[${i}].id`); c.region(a.region, `anchors[${i}].region`); anchorIds.add(a.id); });
  obs.observations.forEach((o, i) => {
    const at = `observations[${i}]`;
    c.id(o.id, 'ob', `${at}.id`);
    c.region(o.region, `${at}.region`);
    c.check(inUnit(o.confidence), `${at}.confidence: must be in [0, 1]`);
    c.check(o.time.startS >= 0 && o.time.endS >= o.time.startS, `${at}.time: ends before it starts`);
    c.check(o.time.endS <= cap.durationS, `${at}.time: reference is outside the capture`);
    c.check(obs.motionSupported || o.kind !== 'movement', `${at}: movement observations require motion-supporting evidence`);
  });

  c.id(edit.id, 'edit', 'streetEdit.id');
  c.check(edit.observationBatchId === obs.id, 'streetEdit: must reference the observation batch');
  c.call(edit.modelCall, 'carbon', 'streetEdit.modelCall');
  const allowed = new Set(edit.permissions.allowed);
  const forbidden = new Set(edit.permissions.forbidden);
  const contradictory = [...allowed].filter((k) => forbidden.has(k));
  c.check(contradictory.length === 0, `streetEdit.permissions: contradictory permissions: ${contradictory.join(', ')}`);
  c.check(edit.status !== 'compiled' || edit.operations.length > 0, 'streetEdit: a compiled edit needs at least one operation');
  c.check(edit.status === 'compiled' || !!edit.rejectionReason, 'streetEdit: rejected or unclear edits must state a reason');
  edit.operations.forEach((op, i) => {
    const at = `operations[${i}]`;
    c.id(op.id, 'op', `${at}.id`);
    c.region(op.region, `${at}.region`);
    c.check(!forbidden.has(op.target), `${at}: targets forbidden class ${op.target}`);
    c.check(allowed.has(op.target), `${at}: targets ${op.target}, which is not allowed`);
    const missing = op.anchorIds.filter((a) => !anchorIds.has(a));
    c.check(missing.length === 0, `${at}: references unknown anchors ${missing.join(', ')}`);
  });

  c.id(ref.id, 'ref', 'referenceImage.id');
  c.call(ref.modelCall, 'nano_banana', 'referenceImage.modelCall');
  c.hash(ref.imageSha256, 'referenceImage.imageSha256');
  c.check(ref.captureId === cap.id && ref.streetEditId === edit.id && edit.status === 'compiled', 'referenceImage: must follow a compiled edit of this capture');
  c.check(ref.width * 9 === ref.height * 16, 'referenceImage: must be exactly 16:9');

  c.gate(gate, 'referenceGate');
  c.check(gate.subject.kind === 'reference_image' && gate.subject.id === ref.id, 'referenceGate: must judge the reference image');
  c.check(gate.modelCall?.inputHashes?.includes(ref.imageSha256), 'referenceGate: must hash the judged image');

  c.id(world.id, 'world', 'worldSession.id');
  c.call(world.modelCall, 'genie', 'worldSession.modelCall');
  c.check(world.deterministicReplay === false, 'worldSession: Genie sessions never claim deterministic replay');
  const extra = FORBIDDEN_SESSION_KEYS.filter((k) => k in (world as unknown as Rec));
  c.check(extra.length === 0, `worldSession: simulator identity fields are not allowed (${extra.join(', ')})`);
  c.check(world.referenceImageId === ref.id && world.gateDecisionId === gate.id, 'worldSession: must reference the gated image and its decision');
  c.check(gate.effect === 'allow_world_creation', 'worldSession: requires an accepted reference gate');
  const ended = world.status === 'terminated' || world.status === 'failed';
  c.check(ended === (world.terminationReason !== null) && ended === (world.terminatedAt !== null), 'worldSession: termination fields must match status');

  navigationEvents.forEach((e, i) => {
    const at = `navigationEvents[${i}]`;
    c.id(e.id, 'nav', `${at}.id`);
    c.check(e.worldSessionId === world.id, `${at}: belongs to another session`);
    if (e.driver === 'hydrogen') c.call(e.modelCall, 'hydrogen', `${at}.modelCall`);
    else c.check(e.modelCall === null, `${at}: human events carry no model call`);
    if (i > 0) {
      c.check(e.sequence > navigationEvents[i - 1].sequence, `${at}: sequences must be unique and ordered`);
      c.check(e.sessionTimeS >= navigationEvents[i - 1].sessionTimeS, `${at}: session times must be non-decreasing`);
    }
  });
  navigationGates.forEach((g, i) => {
    c.gate(g, `navigationGates[${i}]`);
    c.check(g.subject.kind === 'navigation_window' && g.subject.id === world.id, `navigationGates[${i}]: must judge this world session`);
  });

  const ev = s.evaluation;
  if (ev) {
    c.id(ev.id, 'eval', 'evaluation.id');
    c.check(ev.captureIds.includes(cap.id), 'evaluation: must include this capture');
    c.check(ev.succeeded + ev.failed + ev.unknown === ev.attempts, 'evaluation: unknown outcomes are never dropped');
  }
  return c.errors.length ? { ok: false, errors: c.errors } : { ok: true, value: s };
}
