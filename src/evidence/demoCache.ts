import { useEffect, useState } from 'react';
import { apiUrl } from './api';
import type { SavedCapture } from './api';

/** Footage pre-captured by scripts/cache_demo_footage.py, served at GET /api/demo-cache. */
export interface DemoCacheEntry { kind: 'hls' | 'snapshot'; saved: SavedCapture; extractionId?: string }
export interface DemoCache { createdAt: string; cameras: Record<string, DemoCacheEntry>; dropped: Record<string, string> }

export function useDemoCache(): DemoCache | null {
  const [cache, setCache] = useState<DemoCache | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    // A missing cache (404) is normal before the script runs; the live workflow still works.
    fetch(apiUrl('/api/demo-cache'), { signal: controller.signal })
      .then(response => response.ok ? response.json() : null)
      .then(value => { if (value?.schemaVersion === 'demo-cache.v1' && value.cameras && value.dropped) setCache(value); })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return cache;
}
