/** Client view of GET /api/captures/{cameraId}/buffer (issue #4). */
export interface CaptureBuffer {
  cameraId: string;
  availability: 'not_recording' | 'starting' | 'recording' | 'stale' | 'unavailable';
  bufferedSeconds: number;
  sourceClock?: 'source_reported' | 'unknown';
  earliestReceivedAt?: string | null;
  history: { maxSecondsAgo: number; maxDurationS: number };
}

export interface HistoryOption {
  secondsAgo: number;
  durationS: number;
  label: string;
  available: boolean;
  reason?: string;
}

/**
 * Time choices for a capture. A choice is offered only if the whole interval is
 * already buffered; otherwise it is returned as unavailable with a reason, so
 * the UI never implies history that was not recorded.
 */
export function historyOptions(buffer: CaptureBuffer, durationS: number, candidates = [10, 30, 60, 120]): HistoryOption[] {
  const recording = buffer.availability === 'recording' || buffer.availability === 'stale';
  return candidates.map((secondsAgo) => {
    const label = `${secondsAgo} seconds ago`;
    if (!recording) return { secondsAgo, durationS, label, available: false, reason: 'No recorded history for this camera yet.' };
    if (durationS > buffer.history.maxDurationS) return { secondsAgo, durationS, label, available: false, reason: `At most ${buffer.history.maxDurationS} s per reconstruction.` };
    if (secondsAgo < durationS) return { secondsAgo, durationS, label, available: false, reason: 'The interval would end in the future.' };
    if (secondsAgo > buffer.history.maxSecondsAgo) {
      return { secondsAgo, durationS, label, available: false, reason: `Only ${Math.floor(buffer.history.maxSecondsAgo)} s recorded so far.` };
    }
    return { secondsAgo, durationS, label, available: true };
  });
}
