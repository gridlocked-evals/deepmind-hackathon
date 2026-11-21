import { describe, expect, it } from 'vitest';
import { CAMERA_CATALOG, reconcileCameraDirectories } from './cameras';
import type { CameraSource } from '../types';

const snapshot: CameraSource = {
  id: 'dot-queens', name: 'Queens crossing', kind: 'snapshot', borough: 'Queens',
  coordinates: [-73.734442, 40.673832], provider: 'NYC DOT',
  sourceUrl: 'https://webcams.nyctmc.org/map', playbackUrl: 'https://webcams.nyctmc.org/api/cameras/dot-queens/image',
  capturedAt: null, availability: 'unverified',
};
const live = { status: 'fulfilled' as const, value: { cameras: CAMERA_CATALOG, mode: 'configured' } };
const failed = { status: 'rejected' as const, reason: new Error('Offline') };

describe('citywide camera directory reconciliation', () => {
  it('retains previously loaded borough cameras when a refresh fails', () => {
    const result = reconcileCameraDirectories([...CAMERA_CATALOG, snapshot], live, failed);
    expect(result.status).toBe('stale');
    expect(result.cameras.find(camera => camera.id === snapshot.id)).toMatchObject({ borough: 'Queens', availability: 'stale' });
  });

  it('does not report a connected directory when the DOT API returns unavailable with HTTP 200', () => {
    const result = reconcileCameraDirectories([snapshot], live, {
      status: 'fulfilled', value: { cameras: [], mode: 'unavailable' },
    });
    expect(result.status).toBe('stale');
    expect(result.cameras.some(camera => camera.id === snapshot.id)).toBe(true);
  });

  it('replaces old snapshots after a healthy refresh and deduplicates by source ID', () => {
    const replacement = { ...snapshot, id: 'new-dot-camera' };
    const result = reconcileCameraDirectories([snapshot], live, {
      status: 'fulfilled', value: { cameras: [replacement, replacement], mode: 'connected' },
    });
    expect(result.status).toBe('connected');
    expect(result.cameras.filter(camera => camera.kind === 'snapshot')).toEqual([replacement]);
  });

  it('keeps the catalogue accessible while both directories are offline', () => {
    const result = reconcileCameraDirectories([...CAMERA_CATALOG, snapshot], failed, failed);
    expect(result.status).toBe('offline');
    expect(result.cameras).toHaveLength(CAMERA_CATALOG.length + 1);
  });
});
