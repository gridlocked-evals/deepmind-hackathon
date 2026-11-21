import type { GeminiDriver } from '../agent/types';
import { adversaryScore, attackRecords, LAST_TRIGGER_S, MAX_ATTACKS, MIN_ATTACK_GAP_S, runMetrics } from './adversary';
import type { AdversaryMode, AdversarySettings, AttackRecord } from './adversary';
import { eventTypes } from './events';

function status(row: AttackRecord) {
  if (row.void) return row.void === 'unavoidable' ? 'void: unavoidable at trigger speed' : row.void === 'route_end' ? 'void: route too short' : 'pending';
  const outcome = row.outcome!.replace('_', ' ') + (row.resolution ? ` (${row.resolution.replace('_', ' ')})` : '');
  return `${outcome} · ${row.guardAssisted ? 'guard assisted' : 'no guard'}`;
}

export function AdversaryLane({ settings, onChange, driver }: { settings: AdversarySettings; onChange: (next: AdversarySettings) => void; driver: GeminiDriver }) {
  const trace = driver.replaying ? null : driver.trace;
  const records = trace?.adversary ? attackRecords(trace) : [];
  const run = trace?.adversary ? runMetrics(trace) : null;
  const score = adversaryScore(records);
  const label = (type: string) => eventTypes().find(option => option.type === type)?.label ?? type;
  return <section className="scenario-panel adversary-lane" aria-labelledby="adversary-heading">
    <div className="scenario-heading"><h3 id="adversary-heading">Adversary lane</h3><span>EVAL</span></div>
    <p>An adversary picks which hazard to inject and when: at most {MAX_ATTACKS} per run, {MIN_ATTACK_GAP_S} s apart, triggered by {LAST_TRIGGER_S} s.</p>
    <fieldset disabled={driver.active}>
      <div className="scenario-fields">
        <label>Adversary<select aria-label="Adversary" value={settings.mode} onChange={e => onChange({ ...settings, mode: e.target.value as AdversaryMode })}>
          <option value="off">Off</option><option value="carbon">Carbon</option><option value="random">Random baseline</option>
        </select></label>
        {settings.mode === 'random' && <label>Seed<input aria-label="Random adversary seed" type="number" min="0" step="1" value={settings.seed}
          onChange={e => onChange({ ...settings, seed: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} /></label>}
      </div>
      <label className="adversary-check"><input type="checkbox" checked={settings.guardsIgnoreHazards}
        onChange={e => onChange({ ...settings, guardsIgnoreHazards: e.target.checked })} />Guards ignore injected hazards (only the model can avoid them)</label>
    </fieldset>
    {settings.mode !== 'off' && <small>Adversary runs replace the Scenario Lab event. Start with the driver button above.</small>}
    {trace?.adversary?.error && <p className="driver-error" role="alert">{trace.adversary.error}</p>}
    {trace?.adversary && run && <>
      <p className="adversary-score">
        Unassisted avoided <strong>{score.unassisted.avoided}/{score.unassisted.total}</strong> · Assisted avoided <strong>{score.assisted.avoided}/{score.assisted.total}</strong>
        {score.void > 0 && <> · {score.void} void</>}{score.censored > 0 && <> · {score.censored} censored</>}
      </p>
      <p>Run: {run.progressM.toFixed(1)} m progress · {run.anyContact ? `${run.incidents.length} contact incident${run.incidents.length === 1 ? '' : 's'}` : 'no contact'} · min separation {run.minSeparationM === null ? 'n/a' : `${run.minSeparationM.toFixed(1)} m`} · stopped {run.stoppedWhileTraversableS.toFixed(1)} s on a traversable route</p>
      {records.length ? <ol className="scenario-events adversary-attacks" aria-label="Attacks">
        {records.map(row => <li key={row.eventId}>
          <strong>{label(row.eventType)} · {row.chosenBy === 'carbon' ? 'Carbon' : 'random'}</strong>
          <span>Trigger {row.triggerTime.toFixed(1)}s{row.appliedAt !== null && ` · applied ${row.appliedAt.toFixed(1)}s`} · {status(row)}</span>
          <small>{row.reason}</small>
        </li>)}
      </ol> : <small>No attacks yet{trace.adversary.decisions.length ? ` (${trace.adversary.decisions.length} wait${trace.adversary.decisions.length === 1 ? '' : 's'})` : ''}.</small>}
    </>}
  </section>;
}
