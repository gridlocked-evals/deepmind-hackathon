import { useLayoutEffect, useRef, useState } from 'react';
import type { GeminiDriver } from '../agent/types';
import type { ScenarioBundle } from '../types';
import { eventTypes, parseScenario, preset } from './events';
import type { ScenarioPlan } from './events';
import { compareTraces, downloadJson, replayScene, summarizeTrace } from './trace';

export function ScenarioPanel({ plan, onChange, driver, onReplay, onBaseline }: {
  plan: ScenarioPlan; onChange: (plan: ScenarioPlan) => void; driver: GeminiDriver;
  onReplay: (scene: ScenarioBundle) => void; onBaseline: () => void;
}) {
  const [error, setError] = useState('');
  const importRevision = useRef(0), active = useRef(driver.active);
  active.current = driver.active;
  useLayoutEffect(() => {
    // A file selection belongs to the plan and replay context that opened it.
    // Commit/unmount invalidation also covers a run starting and then stopping.
    importRevision.current++;
    return () => { importRevision.current++; };
  }, [plan, onChange, onReplay, driver.active]);
  const event = plan.events[0], trace = driver.trace;
  const summary = trace ? summarizeTrace(trace) : null;
  const baseline = [...driver.traces].reverse().find(item => item.mode === 'baseline');
  const eventRun = [...driver.traces].reverse().find(item => item.mode === 'event');
  const comparison = baseline && eventRun ? compareTraces(baseline, eventRun) : null;
  const change = (next: ScenarioPlan) => {
    importRevision.current++;
    try { onChange(parseScenario(next)); setError(''); } catch (failure) { setError((failure as Error).message); }
  };
  const patchEvent = (patch: Partial<typeof event>) => change({ ...plan, events: [{ ...event, ...patch }, ...plan.events.slice(1)] });
  const load = async (file?: File) => {
    if (!file || active.current) return;
    const revision = ++importRevision.current;
    try {
      if (file.size > 8_000_000) throw new Error('Choose a JSON file smaller than 8 MB.');
      const text = await file.text();
      if (active.current || revision !== importRevision.current) return;
      const raw = JSON.parse(text);
      if (['gridlocked-driver-trace.v1', 'gridlocked-driver-trace.v2'].includes(raw.schemaVersion)) {
        const scene = replayScene(raw);
        onChange(parseScenario(raw.plan));
        onReplay(scene);
      }
      else change(parseScenario(raw));
      setError('');
    } catch (failure) {
      if (!active.current && revision === importRevision.current) setError((failure as Error).message);
    }
  };
  return <section className="scenario-panel" aria-labelledby="scenario-heading">
    <div className="scenario-heading"><h3 id="scenario-heading">Make something happen</h3><span>SCENARIO LAB</span></div>
    <p>Watch a crash unfold, then inspect how the driver reacts.</p>
    <fieldset disabled={driver.active}>
      <label>Event<select aria-label="Scenario preset" value={event.type} onChange={e => change(preset(e.target.value))}>
        {eventTypes().map(option => <option key={option.type} value={option.type}>{option.label}</option>)}
      </select></label>
      <div className="scenario-fields">
        <label>Trigger<select aria-label="Event trigger" value={event.trigger.kind === 'manual' ? 'manual' : 'time'} onChange={e => patchEvent({ trigger: e.target.value === 'manual' ? { kind: 'manual' } : { kind: 'time', seconds: 3 } })}>
          <option value="time">Simulation time</option><option value="manual">Manual</option>
        </select></label>
        {event.trigger.kind !== 'manual' && <label>At second<input aria-label="Event time in seconds" type="number" min="0" max="24" value={event.trigger.kind === 'time' ? event.trigger.seconds : event.trigger.frame * .1} onChange={e => patchEvent({ trigger: { kind: 'time', seconds: Number(e.target.value) } })} /></label>}
      </div>
      <div className="scenario-fields">
        <label>Distance ahead (m)<input aria-label="Hazard distance in meters" type="number" min="20" max="100" value={event.parameters.distanceAheadM} onChange={e => patchEvent({ parameters: { ...event.parameters, distanceAheadM: Number(e.target.value) } })} /></label>
        <label>Clear after (s)<input aria-label="Hazard duration in seconds" type="number" min="1" max="20" value={event.durationSeconds} onChange={e => patchEvent({ durationSeconds: Number(e.target.value) })} /></label>
      </div>
      <div className="scenario-file-controls"><label className="scenario-file">Import scenario / replay<input aria-label="Import scenario or replay JSON" type="file" accept=".json,application/json" onChange={e => { void load(e.target.files?.[0]); e.target.value = ''; }} /></label>
        <button type="button" onClick={() => downloadJson(plan, `${plan.id}.json`)}>Save scenario</button></div>
    </fieldset>
    {error && <p className="driver-error" role="alert">{error}</p>}
    <div className="scenario-events" aria-live="polite">
      {plan.events.map(item => {
        const rows = trace?.plan.id === plan.id && driver.hasRun ? trace.eventLog.filter(row => row.eventId === item.id) : [];
        const last = rows.at(-1);
        return <div key={item.id}><strong>{eventTypes().find(option => option.type === item.type)?.label}</strong>
          <span>{driver.replaying ? 'Recorded playback · no new injection' : driver.hasRun && trace?.mode === 'baseline' ? 'Disabled for baseline' : driver.hasRun && trace?.adversary ? 'Disabled for adversary run' : last ? `${last.kind.replaceAll('_', ' ')} · ${last.time.toFixed(1)}s` : item.trigger.kind === 'manual' ? 'Ready for a manual trigger' : `Scheduled · ${(item.trigger.kind === 'time' ? item.trigger.seconds : item.trigger.frame * .1).toFixed(1)}s`}</span>
          {item.trigger.kind === 'manual' && <button type="button" disabled={!driver.active || trace?.mode === 'baseline' || !!trace?.adversary || trace?.plan.id !== plan.id || rows.some(row => row.kind === 'requested' || row.kind === 'applied')} onClick={() => driver.inject(item.id)}>Inject event</button>}
        </div>;
      })}
    </div>
    <small>Manual triggers apply before the next decision. The crash is scripted; Gemini controls the following car.</small>
    <button type="button" className="scenario-baseline" disabled={!driver.canStart} onClick={() => { importRevision.current++; onBaseline(); }}>Run baseline without hazards</button>
    {trace && summary && !driver.replaying && <details className="scenario-results" open={trace.terminal.reason !== 'running'}>
      <summary>{trace.mode === 'baseline' ? 'Baseline' : 'Event run'} · {trace.terminal.reason.replaceAll('-', ' ')}</summary>
      <p>{summary.progressM.toFixed(1)} m travelled · {summary.scriptedCrashCount} scripted crash{summary.scriptedCrashCount === 1 ? '' : 'es'}</p>
      <p>Controller interventions: {summary.controllerInterventionFrames} frames. Ego contacts: {summary.egoContactFrames} sampled frames ({summary.injectedHazardContactFrames} with injected hazards; {summary.replayedTrafficContactFrames} with replayed traffic).</p>
      {summary.replayedTrafficContactFrames > 0 && <small>Replayed traffic does not react to ego and can pass through it. These overlaps are retained, not scored as model failures.</small>}
      {summary.events.map(row => <p key={row.id}>Model reaction: {row.responseLatencySimulationSeconds === null ? 'not observed' : `${row.responseLatencySimulationSeconds.toFixed(1)} sim s after first input`}. Stop: {row.stoppedAt === null ? 'not observed' : `${row.stoppedAt.toFixed(1)}s`}. Recovery: {row.resumedAt === null ? 'not observed' : `${row.resumedAt.toFixed(1)}s`}.</p>)}
      <div className="scenario-file-controls"><button type="button" onClick={() => downloadJson(trace, `gridlocked-${trace.mode}-${trace.runId}.json`)}>Export trace</button>
        <button type="button" disabled={driver.active} onClick={() => { importRevision.current++; const scene = replayScene(trace); onChange(parseScenario(trace.plan)); onReplay(scene); }}>Replay recorded run</button></div>
      {comparison && <><p>Baseline: {comparison.baseline.progressM.toFixed(1)} m · Event: {comparison.event.progressM.toFixed(1)} m. Descriptive comparison; model decisions can vary.</p><button type="button" onClick={() => downloadJson({ ...comparison, runs: [baseline, eventRun] }, 'gridlocked-comparison.json')}>Export comparison</button></>}
      <small>Go around uses a clear neighboring lane; changing the destination route is unsupported. Recorded replays make no model calls.</small>
    </details>}
    {driver.traces.length > 1 && <details className="scenario-results"><summary>Saved runs in this tab ({driver.traces.length})</summary>
      {driver.traces.map(item => <button type="button" key={item.runId} onClick={() => downloadJson(item, `gridlocked-${item.mode}-${item.runId}.json`)}>Export {item.mode} · {item.createdAt.slice(11,19)}</button>)}</details>}
    <small>The last four completed or interrupted runs survive reset in this tab. Export before reloading the page.</small>
  </section>;
}
