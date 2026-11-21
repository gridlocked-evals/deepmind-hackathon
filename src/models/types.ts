/** Read-only /api/models/status contract. No browser-side model credentials. */
export type ModelService = 'hydrogen' | 'carbon' | 'nano_banana' | 'decisions' | 'genie';
export type CapabilityState = 'configured' | 'unverified' | 'connected' | 'stale' | 'unavailable';

export interface ModelGatewayStatus {
  schema_version: 1;
  config_hash: string;
  rates_expired: boolean;
  services: {
    service: ModelService;
    model_id: string;
    state: CapabilityState;
    reason: string | null;
    configured: boolean;
    last_request_id: string | null;
    last_provider_request_id: string | null;
    last_latency_ms: number | null;
    /** Server UTC Unix seconds; access verification time, not camera exposure time. */
    verified_at: number | null;
    usage_reported: boolean;
    project_quota: string;
  }[];
  budgets: {
    charged_usd: number;
    limit_usd: number;
    demo_reserve_usd: number;
    stages: Record<string, { charged_usd: number; charged_tokens: number }>;
    blocks: { reason: string; request_id: string }[];
    unresolved_world_requests: string[];
    notice: string;
  };
}
