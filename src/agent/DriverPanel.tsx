import { ArrowUpRight, BrainCircuit, Play, Square } from 'lucide-react';
import { MAX_DRIVER_STEPS } from './types';
import type { GeminiDriver } from './types';
import { ScenarioPanel } from '../scenarios/ScenarioPanel';
import type { ScenarioPlan } from '../scenarios/events';
import type { ScenarioBundle } from '../types';

export interface DriverPanelProps {
  driver: GeminiDriver;
  onStart?: (mode?: 'baseline' | 'event') => void;
  plan: ScenarioPlan;
  onPlanChange: (plan: ScenarioPlan) => void;
  onReplay: (scene: ScenarioBundle) => void;
}

export function DriverPanel({ driver, onStart, plan, onPlanChange, onReplay }: DriverPanelProps) {
  const latest = driver.decisionLog.at(-1);
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
    <p className="driver-subtitle">Gemini sets speed; steering follows route.</p>
    <div className="driver-metrics">
      <div><strong>{Math.round(driver.speedMps * 2.23694)}<small>mph</small></strong><span>{driver.hasRun ? 'Vehicle speed' : 'Starting speed'}</span></div>
      <div><strong>{Math.round(driver.remainingM)}<small>m</small></strong><span>Route remaining</span></div>
      <div><strong>{driver.decisionLog.length}<small>/{MAX_DRIVER_STEPS}</small></strong><span>Decisions</span></div>
    </div>
    <div className="driver-decision">
      <div className="driver-decision-heading" role="status"><ArrowUpRight size={15} aria-hidden="true" /><strong>{statusLabel}</strong></div>
      {latest ? <p><span className="driver-action">{latest.decision.action}</span> · {latest.decision.reason}</p>
        : <p>Give Gemini the wheel for up to 36 seconds of simulation.</p>}
      {driver.status === 'thinking' && <small>Simulation paused while the model responds.</small>}
      {driver.completionReason === 'decision-limit' && <small>The 12-decision limit is reached. Start again for a new run.</small>}
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
      <span className="driver-model">{latest?.model_id ?? driver.modelAvailability.modelId ?? 'Server-side Gemini'}</span>
    </div>
    <ScenarioPanel plan={plan} onChange={onPlanChange} driver={driver} onReplay={onReplay} onBaseline={() => (onStart ?? driver.start)('baseline')} />
    {driver.decisionLog.length > 0 && <details className="driver-history">
      <summary>Decision history <span>{driver.decisionLog.length}</span></summary>
      <ol>{driver.decisionLog.map(entry => <li key={entry.request_id}>
        <div><strong>{entry.decision.action}</strong><span>{entry.simulationTime.toFixed(1)}s · {Math.round(entry.decision.target_speed_mps * 2.23694)} mph target</span></div>
        <p>{entry.decision.reason}</p>
      </li>)}</ol>
    </details>}
    <p className="driver-footnote">Uses simulated state; surrounding traffic is replayed. Safety limits apply. Camera perception is being connected.</p>
  </section>;
}

export default DriverPanel;
