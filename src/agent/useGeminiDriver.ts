import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { mediaUrl } from '../media/cameras';
import type { ModelGatewayStatus } from '../models/types';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';
import { createDriverSimulation, driverEligibility, driverFrame, validateEventRun } from './physics';
import type { DriverSimulation } from './physics';
import { DriverRunGate, validateDecisionResponse } from './session';
import { runContinuousDriver } from './continuous';
import { createTrace, DriverRequestError, finalizeHold, finishTrace, recordTick, startHold, traceError } from './trace';
import type { DriverTrace, HoldRecorder, TraceTerminal } from './trace';
import type { DecisionEntry, DriverAvailability, DriverStatus, GeminiDriver } from './types';
import { createEvents, requestEvent } from '../scenarios/events';
import type { ScenarioPlan } from '../scenarios/events';
import { beginDecision, createTrace as createScenarioTrace, finishTrace as finishScenarioTrace, recordFrame } from '../scenarios/trace';
import type { RunTrace, Terminal, TraceStep } from '../scenarios/trace';
import { ADVERSARY_OFF, adversaryBarrier, attackRecords, runMetrics } from '../scenarios/adversary';
import type { AdversarySettings } from '../scenarios/adversary';

function initialSimulation(scenario: ScenarioBundle) {
  return driverEligibility(scenario) === null ? createDriverSimulation(scenario) : null;
}

/** Decisions update a continuously animated controller; gateway retry limits still apply. */
export function useGeminiDriver(scenario: ScenarioBundle, plan: ScenarioPlan, adversary: AdversarySettings = ADVERSARY_OFF): GeminiDriver {
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
  const traceRef = useRef<RunTrace | null>(null);
  const pendingDecision = useRef<{ entry: TraceStep; requestedAt: number } | null>(null);
  const [traces, setTraces] = useState<RunTrace[]>([]);
  const [, refreshEvents] = useState(0);
  const [modelAvailability, setModelAvailability] = useState<DriverAvailability>({ state: 'checking', modelId: null, reason: null });

  const publish = useCallback((state: DriverSimulation) => {
    setTime(state.time); setSpeedMps(state.speed); setRemainingM(Math.max(0, state.path.length - state.progress));
  }, []);

  const finish = useCallback((reason: Terminal, message: string | null = null) => {
    const scenarioTrace = traceRef.current;
    if (!scenarioTrace || scenarioTrace.terminal.reason !== 'running' || !simulation.current) return;
    if (pendingDecision.current) pendingDecision.current.entry.wallLatencyMs = performance.now() - pendingDecision.current.requestedAt;
    finishScenarioTrace(scenarioTrace, simulation.current, reason, message);
    if (scenarioTrace.adversary) Object.assign(scenarioTrace.adversary, { attacks: attackRecords(scenarioTrace), run: runMetrics(scenarioTrace) });
    if (trace.current) scenarioTrace.driverTrace = structuredClone(trace.current);
    const saved = structuredClone(scenarioTrace);
    setTraces(items => [...items.filter(item => item.runId !== saved.runId).slice(-3), saved]);
  }, []);

  const halt = useCallback((terminal: TraceTerminal) => {
    const wasActive = activeRef.current;
    activeRef.current = false;
    gate.current.cancel();
    // Record the partial hold before the terminal state, so an export right after Stop already includes it.
    if (wasActive) { finalizeCurrentHold.current?.(); finishTrace(trace.current, terminal); finish('interrupted'); setStatus('stopped'); if (simulation.current) publish(simulation.current); }
  }, [publish, finish]);
  const stop = useCallback(() => halt({ state: 'stopped_by_user' }), [halt]);

  const reset = useCallback(() => {
    activeRef.current = false;
    gate.current.cancel();
    finalizeCurrentHold.current?.();
    finalizeCurrentHold.current = null;
    finishTrace(trace.current, { state: 'interrupted' });
    finish('reset');
    traceRef.current = null;
    pendingDecision.current = null;
    trace.current = null;
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
    initial = { ...initial, events: createEvents(plan, mode), ...(adversary.guardsIgnoreHazards ? { guardsIgnoreHazards: true } : {}) };
    try { validateEventRun(initial); } catch (failure) { setError((failure as Error).message); setStatus('error'); return; }
    const lease = gate.current.begin(crypto.randomUUID());
    activeRef.current = true;
    simulation.current = initial;
    finalizeCurrentHold.current = null;
    const runTrace = trace.current = createTrace(lease.runId, initial);
    const scenarioTrace = traceRef.current = createScenarioTrace(initial, lease.runId);
    pendingDecision.current = null;
    frameRef.current = driverFrame(initial);
    publish(initial);
    setHasRun(true); setError(null); setDecisionLog([]); setCompletionReason(null); setStatus('thinking');

    let hold: HoldRecorder | null = null;
    const barrier = mode === 'event' ? adversaryBarrier(adversary, scenarioTrace, {
      runId: lease.runId, signal: lease.controller.signal, url: mediaUrl('/api/adversary/attack'),
      isCurrent: () => gate.current.isCurrent(lease),
      read: () => simulation.current ?? initial,
      write: state => { simulation.current = state; recordFrame(scenarioTrace, state); frameRef.current = driverFrame(state); refreshEvents(value => value + 1); },
    }) : undefined;
    void runContinuousDriver({
      barrier,
      initial,
      signal: lease.controller.signal,
      readState: () => simulation.current ?? initial,
      onRequest: (step, observation, state) => {
        if (!gate.current.isCurrent(lease)) return;
        const entry = beginDecision(scenarioTrace, state, step, observation);
        pendingDecision.current = { entry, requestedAt: performance.now() };
      },
      requestDecision: async (step, observation, signal) => {
        const pending = pendingDecision.current;
        try {
          const response = await fetch(mediaUrl('/api/driver/decision'), {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
            body: JSON.stringify({ run_id: lease.runId, step, observation }),
          });
          const payload: unknown = await response.json();
          if (!response.ok) {
            const detail = (payload as { detail?: { message?: unknown; code?: unknown; request_id?: unknown } })?.detail;
            if (pending && gate.current.isCurrent(lease) && !signal.aborted) pending.entry.failure = {
              code: typeof detail?.code === 'string' && /^[a-z_]{1,40}$/.test(detail.code) ? detail.code : 'request_failed',
              requestId: typeof detail?.request_id === 'string' && /^[0-9a-f-]{36}$/i.test(detail.request_id) ? detail.request_id : null,
            };
            throw new DriverRequestError(typeof detail?.message === 'string' ? detail.message.slice(0, 280) : 'The model gateway could not return a driving decision.');
          }
          return validateDecisionResponse(payload, lease.runId, step);
        } finally {
          if (pending && gate.current.isCurrent(lease) && !signal.aborted && scenarioTrace.terminal.reason === 'running') {
            pending.entry.wallLatencyMs = performance.now() - pending.requestedAt;
          }
        }
      },
      onDecision: (response, observation, state) => {
        if (!gate.current.isCurrent(lease)) return;
        const pending = pendingDecision.current;
        if (pending?.entry.step === response.step) {
          pending.entry.response = response;
          pending.entry.appliedSimulationTime = state.time;
          pending.entry.wallLatencyMs = performance.now() - pending.requestedAt;
          pendingDecision.current = null;
        }
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
        recordFrame(scenarioTrace, state);
        frameRef.current = driverFrame(state);
      },
      onPublish: state => { if (gate.current.isCurrent(lease)) publish(state); },
    }).then(reason => {
      if (!gate.current.isCurrent(lease)) return;
      finalizeCurrentHold.current?.();
      finalizeCurrentHold.current = null;
      finishTrace(runTrace, { state: reason === 'route-complete' ? 'route_complete' : reason === 'time-limit' ? 'time_limit' : 'decision_limit' });
      finish(reason);
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
      finish('error', message);
      setError(message);
      setStatus('error');
      gate.current.cancel();
    });
  }, [modelAvailability.state, publish, scenario, plan, finish, adversary]);

  const unavailableReason = driverEligibility(scenario);
  const active = status === 'thinking' || status === 'driving';
  return { frameRef, time, status, active, hasRun, speedMps, remainingM, decisionLog, error, modelAvailability,
    canStart: !active && modelAvailability.state === 'available' && !unavailableReason,
    unavailableReason, completionReason, start, stop, reset, inject, traces, trace: traceRef.current ?? traces.at(-1) ?? null,
    replaying: scenario.motionSource === 'event-replay',
    exportTrace: () => trace.current?.terminal ? trace.current : null,
    refreshAvailability: () => setAvailabilityAttempt(value => value + 1) };
}
