import { useEffect, useState } from 'react';
import type { CameraSource } from '../types';

// Official source coordinates from Pharos 5bd6fd9 / the 511NY registry.
// These are configured sources, not a claim that a camera is currently online.
const SOURCES = [
  ['32998', 'Vestry Street', -74.01148453531307, 40.722891497915406, 'R11_277', '640 × 352'],
  ['32991', 'Houston Street', -74.01051672537487, 40.72924236268597, 'R11_271', '352 × 240'],
  ['32997', 'West 14th Street', -74.00871745903845, 40.742276487161, 'R11_276', '352 × 240'],
  ['32996', 'West 23rd Street', -74.00762318468455, 40.74826078811978, 'R11_275', '352 × 240'],
  ['32995', 'West 42nd Street', -74.00091777520173, 40.762071202304085, 'R11_274', '352 × 240'],
] as const;

export const CAMERA_CATALOG: CameraSource[] = SOURCES.map(([id, street, lon, lat, , resolution]) => ({
  id: `511ny-${id}`, name: `West Street at ${street}`, kind: 'hls',
  coordinates: [lon, lat], provider: '511NY / NYSDOT', sourceUrl: `https://511ny.org/camera/${id}`,
  playbackUrl: `/api/feeds/511ny-${id}/index.m3u8`, resolution,
  description: 'Public roadway video · source clock and broadcast delay unknown',
  capturedAt: null, fetchedAt: null, availability: 'unverified',
}));

const DIRECT_STREAMS = Object.fromEntries(SOURCES.map(([id, , , , stream]) => [
  `511ny-${id}`, `https://s9.nysdot.skyvdn.com/rtplive/${stream}/playlist.m3u8`,
]));

export function directStreamUrl(cameraId: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(DIRECT_STREAMS, cameraId) ? DIRECT_STREAMS[cameraId] : undefined;
}

export function mediaUrl(url: string): string {
  return url.startsWith('/api/') ? new URL(`.${url}`, document.baseURI).href : url;
}

function isCamera(value: unknown): value is CameraSource {
  if (!value || typeof value !== 'object') return false;
  const camera = value as Partial<CameraSource>;
  return typeof camera.id === 'string' && typeof camera.name === 'string'
    && (camera.kind === 'hls' || camera.kind === 'snapshot')
    && Array.isArray(camera.coordinates) && camera.coordinates.length === 2
    && camera.coordinates.every(Number.isFinite) && typeof camera.playbackUrl === 'string'
    && typeof camera.sourceUrl === 'string' && typeof camera.provider === 'string';
}

export type CameraDirectoryStatus = 'loading' | 'connected' | 'stale' | 'offline';

export function useCameraSources(): { cameras: CameraSource[]; status: CameraDirectoryStatus } {
  const [cameras, setCameras] = useState<CameraSource[]>(CAMERA_CATALOG);
  const [status, setStatus] = useState<CameraDirectoryStatus>('loading');
  useEffect(() => {
    const controller = new AbortController();
    let running = false;
    const refresh = async () => {
      if (running || document.hidden) return;
      running = true;
      const request = async (path: string) => {
        const timeout = new AbortController();
        const abort = () => timeout.abort();
        controller.signal.addEventListener('abort', abort, { once: true });
        const timer = window.setTimeout(abort, 12000);
        try {
          const response = await fetch(mediaUrl(path), { signal: timeout.signal });
          if (!response.ok) throw new Error('Camera directory unavailable');
          const data: { cameras?: unknown[]; metadata?: { mode?: string } } = await response.json();
          if (!Array.isArray(data.cameras)) throw new Error('Invalid camera directory');
          return { cameras: data.cameras.filter(isCamera), mode: data.metadata?.mode };
        } finally {
          clearTimeout(timer);
          controller.signal.removeEventListener('abort', abort);
        }
      };
      const results = await Promise.allSettled([request('/api/vision/cameras'), request('/api/cameras')]);
      running = false;
      if (controller.signal.aborted) return;
      const [live, stills] = results;
      const liveCameras = live.status === 'fulfilled' ? live.value.cameras : CAMERA_CATALOG;
      const snapshotCameras = stills.status === 'fulfilled' ? stills.value.cameras : [];
      // The configured catalog remains selectable when the API is offline.
      const merged = new Map(CAMERA_CATALOG.map(camera => [camera.id, camera]));
      for (const camera of [...liveCameras, ...snapshotCameras]) merged.set(camera.id, camera);
      setCameras([...merged.values()]);
      setStatus(live.status === 'rejected' && stills.status === 'rejected' ? 'offline'
        : live.status === 'rejected' || (stills.status === 'fulfilled' && stills.value.mode === 'stale') ? 'stale' : 'connected');
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 60000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { controller.abort(); clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, []);
  return { cameras, status };
}
