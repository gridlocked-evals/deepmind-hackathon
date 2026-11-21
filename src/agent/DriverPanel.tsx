import { ArrowUpRight, BrainCircuit, Download, Play, Square } from 'lucide-react';
import { MAX_DRIVER_STEPS, MAX_DRIVE_SECONDS } from './types';
import type { GeminiDriver } from './types';
import { ScenarioPanel } from '../scenarios/ScenarioPanel';
import type { ScenarioPlan } from '../scenarios/events';
import type { ScenarioBundle } from '../types';
import { AdversaryLane } from '../scenarios/AdversaryLane';
import type { AdversarySettings } from '../scenarios/adversary';

export interface DriverPanelProps {
  driver: GeminiDriver;
  onStart?: (mode?: 'baseline' | 'event') => void;
  plan: ScenarioPlan;
  onPlanChange: (plan: ScenarioPlan) => void;
  onReplay: (scene: ScenarioBundle) => void;
  adversary?: AdversarySettings;
  onAdversaryChange?: (settings: AdversarySettings) => void;
}

function downloadTrace(driver: GeminiDriver) {
  const trace = driver.exportTrace();
  if (!trace) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(trace, null, 2)], { type: 'application/json' }));
  const link = Object.assign(document.createElement('a'), { href: url, download: `gridlocked-driver-trace-${trace.run_id}.json` });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function DriverPanel({ driver, onStart, plan, onPlanChange, onReplay, adversary, onAdversaryChange }: DriverPanelProps) {
  const latest = driver.decisionLog.at(-1);
  const observation = driver.trace?.steps.at(-1)?.observation;
  const statusLabel = driver.replaying ? 'Recorded playback' : driver.status === 'thinking' ? 'Gemini is deciding'
    : driver.status === 'driving' ? 'Driving the route'
    : driver.status === 'complete' ? driver.completionReason === 'route-complete' ? 'Destination reached' : 'Demo complete'
    : driver.status === 'stopped' ? 'Drive stopped'
    : driver.status === 'error' ? 'Drive interrupted' : 'Ready for a driver';
  return <section className="driver-panel" aria-labelledby="driver-heading">
    <div className="driver-header">
      <div className="driver-title"><BrainCircuit size={17} aria-hidden="true" /><h2 id="driver-heading">Gemini driver</h2></div>
      <span className={`driver-status driver-status-${driver.status}`}>{driver.status === 'thinking' ? 'Thinking' : driver.status === 'driving' ? 'Live agent' : 'Simulation'}</span>
    </div>
    <p className="driver-subtitle">Gemini can slow down, stop, or go around an obstruction.</p>
    <div className="driver-metrics">
      <div><strong>{Math.round(driver.speedMps * 2.23694)}<small>mph</small></strong><span>{driver.hasRun ? 'Vehicle speed' : 'Starting speed'}</span></div>
      <div><strong>{Math.round(driver.remainingM)}<small>m</small></strong><span>Route remaining</span></div>
      <div><strong>{driver.decisionLog.length}<small>/{MAX_DRIVER_STEPS}</small></strong><span>Decisions</span></div>
    </div>
    <div className="driver-decision">
      <div className="driver-decision-heading" role="status"><ArrowUpRight size={15} aria-hidden="true" /><strong>{statusLabel}</strong></div>
      {latest ? <p><span className="driver-action">{latest.decision.action.replaceAll('_', ' ')}</span> · {latest.decision.reason}</p>
        : <p>Give Gemini the wheel for up to {MAX_DRIVE_SECONDS} seconds of continuous simulation.</p>}
      {driver.active && observation?.go_around_available && <small>Clear simulated passing path on the {observation.go_around_side}.</small>}
      {driver.active && observation?.go_around_active && !observation.go_around_available && <small>Passing maneuver active; traffic and signal limits apply.</small>}
      {driver.status === 'thinking' && <small>Waiting for the first driving decision.</small>}
      {driver.completionReason === 'time-limit' && <small>The {MAX_DRIVE_SECONDS}-second drive is complete. Start again for a new run.</small>}
      {driver.completionReason === 'decision-limit' && <small>The {MAX_DRIVER_STEPS}-decision limit is reached. Start again for a new run.</small>}
    </div>
    {driver.error && <p className="driver-error" role="alert">{driver.error}</p>}
    {driver.unavailableReason && <p className="driver-notice">{driver.unavailableReason}</p>}
    {driver.modelAvailability.state === 'checking' && <p className="driver-notice">Checking the Gemini gateway…</p>}
    {driver.modelAvailability.state === 'unavailable' && <div className="driver-notice">
      <p>{driver.modelAvailability.reason}</p>
      <button type="button" className="driver-retry" onClick={driver.refreshAvailability}>Check connection</button>
    </div>}
    <div className="driver-controls">
      {driver.active ? <button type="button" className="driver-stop" onClick={driver.stop}><Square size={14} aria-hidden="true" />Stop drive</button>
        : <button type="button" className="driver-start" disabled={!driver.canStart} onClick={() => (onStart ?? driver.start)('event')}><Play size={14} aria-hidden="true" />{driver.hasRun ? 'Restart scenario' : 'Start scenario with Gemini'}</button>}
      {driver.hasRun && !driver.active && <button type="button" className="driver-retry driver-trace" onClick={() => downloadTrace(driver)}><Download size={14} aria-hidden="true" />Download trace (JSON)</button>}
      <span className="driver-model">{latest?.model_id ?? driver.modelAvailability.modelId ?? 'Server-side Gemini'}</span>
    </div>
    <ScenarioPanel plan={plan} onChange={onPlanChange} driver={driver} onReplay={onReplay} onBaseline={() => (onStart ?? driver.start)('baseline')} />
    {adversary && onAdversaryChange && <AdversaryLane settings={adversary} onChange={onAdversaryChange} driver={driver} />}
    {driver.decisionLog.length > 0 && <details className="driver-history">
      <summary>Decision history <span>{driver.decisionLog.length}</span></summary>
      <ol>{driver.decisionLog.map(entry => <li key={entry.request_id}>
        <div><strong>{entry.decision.action.replaceAll('_', ' ')}</strong><span>{entry.simulationTime.toFixed(1)}s · {Math.round(entry.decision.target_speed_mps * 2.23694)} mph target</span></div>
        <p>{entry.decision.reason}</p>
      </li>)}</ol>
    </details>}
    <p className="driver-footnote">Uses simulated state; surrounding traffic is replayed. Safety limits apply. Camera perception is being connected.</p>
  </section>;
}

export default DriverPanel;
