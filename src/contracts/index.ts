import Ajv2020 from 'ajv/dist/2020';
import schema from './schema.json';
import type { Capture, ObservationBatch, StreetEdit, ReferenceImage, GateDecision, ModelCall } from './element119';
export * from './element119';
interface ContractMap { Capture: Capture; ObservationBatch: ObservationBatch; StreetEdit: StreetEdit; ReferenceImage: ReferenceImage; GateDecision: GateDecision; ModelCall: ModelCall }
const ajv = new Ajv2020({ allErrors: true, strict: true });
ajv.addFormat('date-time', value => Number.isFinite(Date.parse(value)));
// Pydantic's discriminator is an OpenAPI hint; oneOf/const perform the validation.
ajv.addKeyword({ keyword: 'discriminator', valid: true });
ajv.addSchema(schema);
const validators = new Map<string, ReturnType<typeof ajv.compile>>();
/** Validate domain payloads against the generated canonical Python schema. */
export function parseContract<K extends keyof ContractMap>(name: K, value: unknown): ContractMap[K] {
  let validate = validators.get(name);
  if (!validate) { validate = ajv.compile({ $ref: `${schema.$id}#/$defs/${name}` }); validators.set(name, validate); }
  if (!validate(value)) throw new Error(`Invalid ${name}: ${ajv.errorsText(validate.errors)}`);
  return value as ContractMap[K];
}
