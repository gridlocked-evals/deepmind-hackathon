import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { mediaUrl } from '../media/cameras';
import type { ModelGatewayStatus } from '../models/types';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { createDriverSimulation, driverEligibility, driverFrame } from './physics';
import type { DriverSimulation } from './physics';
import { DriverRunGate, validateDecisionResponse } from './session';
import { runContinuousDriver } from './continuous';
import { createTrace, DriverRequestError, finalizeHold, finishTrace, recordTick, startHold, traceError } from './trace';
import type { DriverTrace, HoldRecorder, TraceTerminal } from './trace';
import type { DecisionEntry, DriverAvailability, DriverStatus, GeminiDriver } from './types';

function initialSimulation(scenario: ScenarioBundle) {
  return driverEligibility(scenario) === null ? createDriverSimulation(scenario) : null;
}

/** Decisions update a continuously animated controller; gateway retry limits still apply. */
export function useGeminiDriver(scenario: ScenarioBundle): GeminiDriver {
  const gate = useRef(new DriverRunGate());
  const simulation = useRef<DriverSimulation | null>(null);
  const frameRef = useRef(sampleScenario(scenario, 0));
  const activeRef = useRef(false);
  const trace = useRef<DriverTrace | null>(null);
  const finalizeCurrentHold = useRef<(() => void) | null>(null);  // records the in-progress hold, once
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

  const halt = useCallback((terminal: TraceTerminal) => {
    const wasActive = activeRef.current;
    activeRef.current = false;
    gate.current.cancel();
    // Record the partial hold before the terminal state, so an export right after Stop already includes it.
    if (wasActive) { finalizeCurrentHold.current?.(); finishTrace(trace.current, terminal); setStatus('stopped'); if (simulation.current) publish(simulation.current); }
  }, [publish]);
  const stop = useCallback(() => halt({ state: 'stopped_by_user' }), [halt]);

  const reset = useCallback(() => {
    activeRef.current = false;
    gate.current.cancel();
    finalizeCurrentHold.current = null;
    trace.current = null;
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
    const onVisibility = () => { if (document.hidden) halt({ state: 'interrupted' }); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [halt]);

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
        const service = data.services?.find(item => item.service === 'decisions');
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
    finalizeCurrentHold.current = null;
    const runTrace = trace.current = createTrace(lease.runId, initial);
    frameRef.current = driverFrame(initial);
    publish(initial);
    setHasRun(true); setError(null); setDecisionLog([]); setCompletionReason(null); setStatus('thinking');

    let hold: HoldRecorder | null = null;
    void runContinuousDriver({
      initial,
      signal: lease.controller.signal,
      requestDecision: async (step, observation, signal) => {
        const response = await fetch(mediaUrl('/api/driver/decision'), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
          body: JSON.stringify({ run_id: lease.runId, step, observation }),
        });
        const payload: unknown = await response.json();
        if (!response.ok) {
          const detail = (payload as { detail?: { message?: unknown } })?.detail;
          throw new DriverRequestError(typeof detail?.message === 'string' ? detail.message.slice(0, 280) : 'The model gateway could not return a driving decision.');
        }
        return validateDecisionResponse(payload, lease.runId, step);
      },
      onDecision: (response, observation, state) => {
        if (!gate.current.isCurrent(lease)) return;
        // Pending inference belongs to the previous command. Replacement starts a new applied interval.
        finalizeCurrentHold.current?.();
        const applied = hold = startHold(state);
        const finalize = () => {
          if (trace.current === runTrace && simulation.current) finalizeHold(runTrace, response, observation, applied, simulation.current);
        };
        finalizeCurrentHold.current = finalize;
        setDecisionLog(log => [...log, { ...response, simulationTime: state.time }]);
        setStatus('driving');
      },
      onFrame: (before, state) => {
        if (!gate.current.isCurrent(lease)) return;
        simulation.current = state;
        if (hold) recordTick(hold, before, state);
        frameRef.current = driverFrame(state);
      },
      onPublish: state => { if (gate.current.isCurrent(lease)) publish(state); },
    }).then(reason => {
      if (!gate.current.isCurrent(lease)) return;
      finalizeCurrentHold.current?.();
      finalizeCurrentHold.current = null;
      finishTrace(runTrace, { state: reason === 'route-complete' ? 'route_complete' : reason === 'time-limit' ? 'time_limit' : 'decision_limit' });
      setCompletionReason(reason);
      setStatus('complete');
      activeRef.current = false;
      gate.current.cancel();
    }).catch(failure => {
      if (!gate.current.isCurrent(lease)) return;
      finalizeCurrentHold.current?.();
      finalizeCurrentHold.current = null;
      activeRef.current = false;
      const message = traceError(failure);
      finishTrace(runTrace, { state: 'error', error: message });
      setError(message);
      setStatus('error');
      gate.current.cancel();
    });
  }, [modelAvailability.state, publish, scenario]);

  const unavailableReason = driverEligibility(scenario);
  const active = status === 'thinking' || status === 'driving';
  return { frameRef, time, status, active, hasRun, speedMps, remainingM, decisionLog, error, modelAvailability,
    canStart: !active && modelAvailability.state === 'available' && !unavailableReason,
    unavailableReason, completionReason, start, stop, reset,
    exportTrace: () => trace.current?.terminal ? trace.current : null,
    refreshAvailability: () => setAvailabilityAttempt(value => value + 1) };
}
