import { describe, expect, it } from 'vitest';
import { historyOptions, type CaptureBuffer } from './captureHistory';

const buffer = (availability: CaptureBuffer['availability'], maxSecondsAgo: number): CaptureBuffer => ({
  cameraId: '511ny-32998', availability, bufferedSeconds: maxSecondsAgo,
  history: { maxSecondsAgo, maxDurationS: 120 },
});

describe('capture history choices', () => {
  it('offers nothing for a camera that is not recording', () => {
    expect(historyOptions(buffer('not_recording', 0), 10).every((o) => !o.available)).toBe(true);
  });

  it('offers 30 seconds ago only once 30 seconds are buffered', () => {
    const early = historyOptions(buffer('recording', 20), 10);
    expect(early.find((o) => o.secondsAgo === 30)).toMatchObject({ available: false, reason: 'Only 20 s recorded so far.' });
    expect(early.find((o) => o.secondsAgo === 10)?.available).toBe(true);
    expect(historyOptions(buffer('recording', 45), 10).find((o) => o.secondsAgo === 30)?.available).toBe(true);
  });

  it('enforces the duration cap and future intervals', () => {
    expect(historyOptions(buffer('recording', 180), 121).every((o) => !o.available)).toBe(true);
    expect(historyOptions(buffer('recording', 180), 30).find((o) => o.secondsAgo === 10)?.reason).toBe('The interval would end in the future.');
  });
});
