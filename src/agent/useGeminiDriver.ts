import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { mediaUrl } from '../media/cameras';
import type { ModelGatewayStatus } from '../models/types';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { advanceDriver, createDriverSimulation, driverEligibility, driverFrame, prepareDriverDecision, validateEventRun, MAX_PHYSICS_STEP, routeComplete } from './physics';
import type { DriverSimulation } from './physics';
import { DriverRunGate, validateDecisionResponse } from './session';
import type { DriverRunLease } from './session';
import { MAX_DRIVER_STEPS, SECONDS_PER_DECISION } from './types';
import type { DecisionEntry, DriverAvailability, DriverDecision, DriverStatus, GeminiDriver } from './types';
import { createEvents, requestEvent } from '../scenarios/events';
import type { ScenarioPlan } from '../scenarios/events';
import { beginDecision, createTrace, finishTrace, recordFrame } from '../scenarios/trace';
import type { RunTrace, Terminal, TraceStep } from '../scenarios/trace';

function initialSimulation(scenario: ScenarioBundle) {
  return driverEligibility(scenario) === null ? createDriverSimulation(scenario) : null;
}

function aborted() { return new DOMException('Driving stopped', 'AbortError'); }

/** A run has at most 12 decisions and no browser retries; gateway retry limits still apply. */
export function useGeminiDriver(scenario: ScenarioBundle, plan: ScenarioPlan): GeminiDriver {
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
  const traceRef = useRef<RunTrace | null>(null);
  const [traces, setTraces] = useState<RunTrace[]>([]);
  const [, refreshEvents] = useState(0);
  const [modelAvailability, setModelAvailability] = useState<DriverAvailability>({ state: 'checking', modelId: null, reason: null });

  const publish = useCallback((state: DriverSimulation) => {
    setTime(state.time); setSpeedMps(state.speed); setRemainingM(Math.max(0, state.path.length - state.progress));
  }, []);

  const finish = useCallback((reason: Terminal, message: string | null = null) => {
    const trace = traceRef.current;
    if (!trace || trace.terminal.reason !== 'running' || !simulation.current) return;
    finishTrace(trace, simulation.current, reason, message);
    const saved = structuredClone(trace);
    setTraces(items => [...items.filter(item => item.runId !== saved.runId).slice(-3), saved]);
  }, []);

  const stop = useCallback(() => {
    const wasActive = activeRef.current;
    activeRef.current = false;
    gate.current.cancel();
    finish('interrupted');
    if (wasActive) { setStatus('stopped'); if (simulation.current) publish(simulation.current); }
  }, [publish, finish]);

  const reset = useCallback(() => {
    activeRef.current = false;
    gate.current.cancel();
    finish('reset');
    traceRef.current = null;
    simulation.current = initialSimulation(scenario);
    frameRef.current = simulation.current ? driverFrame(simulation.current) : sampleScenario(scenario, 0);
    setTime(0); setSpeedMps(simulation.current?.speed ?? 0);
    setRemainingM(simulation.current ? simulation.current.path.length - simulation.current.progress : 0);
    setStatus('idle'); setHasRun(false); setError(null); setDecisionLog([]); setCompletionReason(null);
  }, [scenario, finish]);

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

  const inject = useCallback((id: string) => {
    if (!activeRef.current || !simulation.current?.events) return;
    simulation.current = { ...simulation.current, events: requestEvent(simulation.current.events, id, simulation.current.time) };
    if (traceRef.current) recordFrame(traceRef.current, simulation.current);
    refreshEvents(value => value + 1);
  }, []);

  const start = useCallback((mode: 'baseline' | 'event' = 'event') => {
    if (activeRef.current || document.hidden || modelAvailability.state !== 'available' || driverEligibility(scenario)) return;
    let initial = initialSimulation(scenario);
    if (!initial) return;
    initial = { ...initial, events: createEvents(plan, mode) };
    try { validateEventRun(initial); } catch (failure) { setError((failure as Error).message); setStatus('error'); return; }
    const lease = gate.current.begin(crypto.randomUUID());
    activeRef.current = true;
    simulation.current = initial;
    traceRef.current = createTrace(initial, lease.runId);
    frameRef.current = driverFrame(initial);
    publish(initial);
    setHasRun(true); setError(null); setDecisionLog([]); setCompletionReason(null); setStatus('thinking');

    const driveSegment = (decision: DriverDecision, run: DriverRunLease) => new Promise<void>((resolve, reject) => {
      if (!gate.current.isCurrent(run) || !simulation.current) { reject(aborted()); return; }
      const until = simulation.current.time + SECONDS_PER_DECISION;
      let animation = 0, previous: number | null = null, published = 0, accumulator = 0;
      const signal = run.controller.signal;
      const cancel = () => { cancelAnimationFrame(animation); signal.removeEventListener('abort', cancel); reject(aborted()); };
      const finish = () => { signal.removeEventListener('abort', cancel); resolve(); };
      signal.addEventListener('abort', cancel, { once: true });
      const tick = (now: number) => {
        if (!gate.current.isCurrent(run) || !simulation.current) { cancel(); return; }
        accumulator += Math.min(.25, previous === null ? 0 : (now - previous) / 1000);
        previous = now;
        while (accumulator >= MAX_PHYSICS_STEP && simulation.current.time < until - 1e-7 && !routeComplete(simulation.current)) {
          simulation.current = advanceDriver(simulation.current, decision, MAX_PHYSICS_STEP);
          if (traceRef.current) recordFrame(traceRef.current, simulation.current);
          accumulator -= MAX_PHYSICS_STEP;
        }
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
          simulation.current = prepareDriverDecision(simulation.current);
          if (!traceRef.current) return;
          recordFrame(traceRef.current, simulation.current);
          frameRef.current = driverFrame(simulation.current);
          setStatus('thinking');
          const entry: TraceStep = beginDecision(traceRef.current, simulation.current, step);
          const observation = entry.observation;
          const requestedAt = performance.now();
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
              const detail = (payload as { detail?: { message?: unknown; code?: unknown; request_id?: unknown } })?.detail;
              entry.failure = { code: typeof detail?.code === 'string' && /^[a-z_]{1,40}$/.test(detail.code) ? detail.code : 'request_failed',
                requestId: typeof detail?.request_id === 'string' && /^[0-9a-f-]{36}$/i.test(detail.request_id) ? detail.request_id : null };
              throw new Error(typeof detail?.message === 'string' ? detail.message.slice(0, 280) : 'The model gateway could not return a driving decision.');
            }
          } catch (failure) {
            if (!gate.current.isCurrent(lease)) return;
            if (request.signal.aborted) throw new Error('The Gemini request timed out. Driving stopped without a fallback.');
            throw failure;
          } finally {
            entry.wallLatencyMs = performance.now() - requestedAt;
            window.clearTimeout(timeout);
            lease.controller.signal.removeEventListener('abort', abortRequest);
          }
          if (!gate.current.isCurrent(lease)) return;
          const response = validateDecisionResponse(payload, lease.runId, step);
          entry.response = response; entry.wallLatencyMs = performance.now() - requestedAt;
          setDecisionLog(log => [...log, { ...response, simulationTime: observation.simulation_time }]);
          setStatus('driving');
          await driveSegment(response.decision, lease);
          if (!gate.current.isCurrent(lease) || !simulation.current) return;
          if (routeComplete(simulation.current)) {
            finish('route-complete');
            setCompletionReason('route-complete'); setStatus('complete'); activeRef.current = false; return;
          }
        }
        if (gate.current.isCurrent(lease)) {
          finish('decision-limit');
          setCompletionReason('decision-limit'); setStatus('complete'); activeRef.current = false;
        }
      } catch (failure) {
        if (!gate.current.isCurrent(lease)) return;
        finish('error', failure instanceof Error ? failure.message : 'Driver request failed.');
        activeRef.current = false;
        setError(failure instanceof Error ? failure.message : 'The driving request failed.');
        setStatus('error'); gate.current.cancel();
      }
    })();
  }, [modelAvailability.state, publish, scenario, plan, finish]);

  const unavailableReason = driverEligibility(scenario);
  const active = status === 'thinking' || status === 'driving';
  return { frameRef, time, status, active, hasRun, speedMps, remainingM, decisionLog, error, modelAvailability,
    canStart: !active && modelAvailability.state === 'available' && !unavailableReason,
    unavailableReason, completionReason, start, stop, reset, inject, traces, trace: traceRef.current ?? traces.at(-1) ?? null,
    replaying: scenario.motionSource === 'event-replay',
    refreshAvailability: () => setAvailabilityAttempt(value => value + 1) };
}
