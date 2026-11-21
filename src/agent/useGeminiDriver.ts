import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { mediaUrl } from '../media/cameras';
import type { ModelGatewayStatus } from '../models/types';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { advanceDriver, createDriverSimulation, driverEligibility, driverFrame, driverObservation, MAX_PHYSICS_STEP, routeComplete } from './physics';
import type { DriverSimulation } from './physics';
import { DriverRunGate, validateDecisionResponse } from './session';
import type { DriverRunLease } from './session';
import { MAX_DRIVER_STEPS, SECONDS_PER_DECISION } from './types';
import type { DecisionEntry, DriverAvailability, DriverDecision, DriverStatus, GeminiDriver } from './types';

function initialSimulation(scenario: ScenarioBundle) {
  return driverEligibility(scenario) === null ? createDriverSimulation(scenario) : null;
}

function aborted() { return new DOMException('Driving stopped', 'AbortError'); }

/** A run is at most 12 model calls; neither request errors nor cancellations trigger retries. */
export function useGeminiDriver(scenario: ScenarioBundle): GeminiDriver {
  const gate = useRef(new DriverRunGate());
  const simulation = useRef<DriverSimulation | null>(null);
  const frameRef = useRef(sampleScenario(scenario, 0));
  const activeRef = useRef(false);
  const [status, setStatus] = useState<DriverStatus>('idle');
  const [hasRun, setHasRun] = useState(false);
  const [time, setTime] = useState(0);
  const [speedMps, setSpeedMps] = useState(0);
  const [remainingM, setRemainingM] = useState(0);
  const [decisionLog, setDecisionLog] = useState<DecisionEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [completionReason, setCompletionReason] = useState<GeminiDriver['completionReason']>(null);
  const [availabilityAttempt, setAvailabilityAttempt] = useState(0);
  const [modelAvailability, setModelAvailability] = useState<DriverAvailability>({ state: 'checking', modelId: null, reason: null });

  const publish = useCallback((state: DriverSimulation) => {
    setTime(state.time); setSpeedMps(state.speed); setRemainingM(Math.max(0, state.path.length - state.progress));
  }, []);

  const stop = useCallback(() => {
    const wasActive = activeRef.current;
    activeRef.current = false;
    gate.current.cancel();
    if (wasActive) { setStatus('stopped'); if (simulation.current) publish(simulation.current); }
  }, [publish]);

  const reset = useCallback(() => {
    activeRef.current = false;
    gate.current.cancel();
    simulation.current = initialSimulation(scenario);
    frameRef.current = simulation.current ? driverFrame(simulation.current) : sampleScenario(scenario, 0);
    setTime(0); setSpeedMps(simulation.current?.speed ?? 0);
    setRemainingM(simulation.current ? simulation.current.path.length - simulation.current.progress : 0);
    setStatus('idle'); setHasRun(false); setError(null); setDecisionLog([]); setCompletionReason(null);
  }, [scenario]);

  useLayoutEffect(() => {
    reset();
    return () => { activeRef.current = false; gate.current.cancel(); };
  }, [reset]);

  useEffect(() => {
    const onVisibility = () => { if (document.hidden) stop(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [stop]);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    let disposed = false;
    setModelAvailability({ state: 'checking', modelId: null, reason: null });
    void (async () => {
      try {
        const response = await fetch(mediaUrl('/api/models/status'), { signal: controller.signal });
        if (!response.ok) throw new Error('Model gateway is unavailable. Start the API and check again.');
        const data = await response.json() as ModelGatewayStatus;
        const service = data.services?.find(item => item.service === 'hydrogen');
        if (disposed) return;
        setModelAvailability({ state: service?.configured ? 'available' : 'unavailable',
          modelId: service?.model_id ?? null, reason: service?.configured ? null : 'Configure the Gemini gateway on the server to start driving.' });
      } catch (failure) {
        if (!disposed) setModelAvailability({ state: 'unavailable', modelId: null,
          reason: failure instanceof Error && failure.name !== 'AbortError' ? failure.message : 'Model gateway did not respond. Check the API connection.' });
      } finally { window.clearTimeout(timeout); }
    })();
    return () => { disposed = true; controller.abort(); window.clearTimeout(timeout); };
  }, [availabilityAttempt]);

  const start = useCallback(() => {
    if (activeRef.current || document.hidden || modelAvailability.state !== 'available' || driverEligibility(scenario)) return;
    const initial = initialSimulation(scenario);
    if (!initial) return;
    const lease = gate.current.begin(crypto.randomUUID());
    activeRef.current = true;
    simulation.current = initial;
    frameRef.current = driverFrame(initial);
    publish(initial);
    setHasRun(true); setError(null); setDecisionLog([]); setCompletionReason(null); setStatus('thinking');

    const driveSegment = (decision: DriverDecision, run: DriverRunLease) => new Promise<void>((resolve, reject) => {
      if (!gate.current.isCurrent(run) || !simulation.current) { reject(aborted()); return; }
      const until = simulation.current.time + SECONDS_PER_DECISION;
      let animation = 0, previous: number | null = null, published = 0;
      const signal = run.controller.signal;
      const cancel = () => { cancelAnimationFrame(animation); signal.removeEventListener('abort', cancel); reject(aborted()); };
      const finish = () => { signal.removeEventListener('abort', cancel); resolve(); };
      signal.addEventListener('abort', cancel, { once: true });
      const tick = (now: number) => {
        if (!gate.current.isCurrent(run) || !simulation.current) { cancel(); return; }
        const delta = Math.min(MAX_PHYSICS_STEP, previous === null ? 0 : (now - previous) / 1000, until - simulation.current.time);
        previous = now;
        simulation.current = advanceDriver(simulation.current, decision, delta);
        frameRef.current = driverFrame(simulation.current);
        const done = simulation.current.time >= until - 0.00001 || routeComplete(simulation.current);
        if (now - published >= 100 || done) { publish(simulation.current); published = now; }
        if (done) finish(); else animation = requestAnimationFrame(tick);
      };
      animation = requestAnimationFrame(tick);
    });

    void (async () => {
      try {
        for (let step = 0; step < MAX_DRIVER_STEPS; step++) {
          if (!gate.current.isCurrent(lease) || !simulation.current) return;
          setStatus('thinking');
          const observation = driverObservation(simulation.current);
          // Simulation does not advance while awaiting the actual model response.
          const request = new AbortController();
          const abortRequest = () => request.abort();
          lease.controller.signal.addEventListener('abort', abortRequest, { once: true });
          const timeout = window.setTimeout(() => request.abort(), 130_000);
          let payload: unknown;
          try {
            const response = await fetch(mediaUrl('/api/driver/decision'), {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: request.signal,
              body: JSON.stringify({ run_id: lease.runId, step, observation }),
            });
            payload = await response.json();
            if (!response.ok) {
              const detail = (payload as { detail?: { message?: unknown } })?.detail;
              throw new Error(typeof detail?.message === 'string' ? detail.message.slice(0, 280) : 'The model gateway could not return a driving decision.');
            }
          } catch (failure) {
            if (!gate.current.isCurrent(lease)) return;
            if (request.signal.aborted) throw new Error('The Gemini request timed out. Driving stopped without a fallback.');
            throw failure;
          } finally {
            window.clearTimeout(timeout);
            lease.controller.signal.removeEventListener('abort', abortRequest);
          }
          if (!gate.current.isCurrent(lease)) return;
          const response = validateDecisionResponse(payload, lease.runId, step);
          setDecisionLog(log => [...log, { ...response, simulationTime: observation.simulation_time }]);
          setStatus('driving');
          await driveSegment(response.decision, lease);
          if (!gate.current.isCurrent(lease) || !simulation.current) return;
          if (routeComplete(simulation.current)) {
            setCompletionReason('route-complete'); setStatus('complete'); activeRef.current = false; return;
          }
        }
        if (gate.current.isCurrent(lease)) {
          setCompletionReason('decision-limit'); setStatus('complete'); activeRef.current = false;
        }
      } catch (failure) {
        if (!gate.current.isCurrent(lease)) return;
        activeRef.current = false;
        setError(failure instanceof Error ? failure.message : 'The driving request failed.');
        setStatus('error'); gate.current.cancel();
      }
    })();
  }, [modelAvailability.state, publish, scenario]);

  const unavailableReason = driverEligibility(scenario);
  const active = status === 'thinking' || status === 'driving';
  return { frameRef, time, status, active, hasRun, speedMps, remainingM, decisionLog, error, modelAvailability,
    canStart: !active && modelAvailability.state === 'available' && !unavailableReason,
    unavailableReason, completionReason, start, stop, reset,
    refreshAvailability: () => setAvailabilityAttempt(value => value + 1) };
}
