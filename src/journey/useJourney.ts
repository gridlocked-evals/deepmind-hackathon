import { useCallback, useEffect, useRef, useState } from 'react';
import { mediaUrl } from '../media/cameras';
import { fetchStreetRoute } from '../navigation/routing';
import type { ScenarioBundle, SimulationFrame } from '../types';
import type { RunTrace } from '../scenarios/trace';
import { emptySnapshot, runJourney } from './runtime';
import { requestJourneyDecision } from './request';

export function useJourney() {
  const lease = useRef<AbortController | null>(null);
  const frameRef = useRef<SimulationFrame>({ time: 0, signal: 'green', actors: [] });
  const traces = useRef<RunTrace[]>([]);
  // Reset retires the UI lease immediately, but its final trace still belongs to
  // this export until the user explicitly starts a new journey.
  const traceOwner = useRef<AbortController | null>(null);
  const [scene, setScene] = useState<ScenarioBundle | null>(null);
  const [snapshot, setSnapshot] = useState(emptySnapshot);
  const [status, setStatus] = useState<'idle' | 'running' | 'stopped' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const stop = useCallback(() => { lease.current?.abort(); setStatus(current => current === 'running' ? 'stopped' : current); }, []);
  const reset = useCallback(() => { lease.current?.abort(); lease.current = null; setScene(null); setStatus('idle'); setError(null); }, []);
  useEffect(() => {
    const hidden = () => { if (document.hidden) stop(); };
    document.addEventListener('visibilitychange', hidden);
    return () => { lease.current?.abort(); lease.current = null; document.removeEventListener('visibilitychange', hidden); };
  }, [stop]);

  const start = useCallback(() => {
    if (document.hidden || (lease.current && !lease.current.signal.aborted)) return;
    const controller = lease.current = new AbortController();
    traceOwner.current = controller;
    const current = () => lease.current === controller;
    traces.current = []; setScene(null); setSnapshot(emptySnapshot()); setError(null); setStatus('running');
    void runJourney({ signal: controller.signal, route: fetchStreetRoute, adversaryUrl: mediaUrl('/api/adversary/attack'),
      driver: requestJourneyDecision,
      onScene(next, frame) { if (current() && !controller.signal.aborted) { frameRef.current = frame; setScene(next); } },
      onFrame(frame) { if (current() && !controller.signal.aborted) frameRef.current = frame; },
      onUpdate(next) { if (current()) setSnapshot(next); },
      onTrace(trace) { if (traceOwner.current === controller) traces.current = [...traces.current.slice(-3), structuredClone(trace)]; },
    }).catch(failure => {
      if (!current() || controller.signal.aborted) return;
      setError(failure instanceof Error ? failure.message : 'Journey interrupted.'); setStatus('error');
    }).finally(() => {
      if (current()) { controller.abort(); setStatus(value => value === 'running' ? 'stopped' : value); }
    });
  }, []);

  const exportReport = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ schemaVersion: 'gridlocked-journey.v1',
      provenance: 'synthetic-structured-state', status, snapshot: { ...snapshot, trace: undefined },
      retainedWindows: traces.current, retention: 'Last 4 completed windows, 80 attacks and 40 restarts.' }, null, 2)], { type: 'application/json' }));
    const link = Object.assign(document.createElement('a'), { href: url, download: 'gridlocked-nyc-journey.json' });
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return { scene, frameRef, snapshot, status, error, active: status === 'running', start, stop, reset, exportReport };
}
export type Journey = ReturnType<typeof useJourney>;
