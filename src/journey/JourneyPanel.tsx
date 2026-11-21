import { Bot, Download, Play, Square, X } from 'lucide-react';
import type { Journey } from './useJourney';
import { eventTypes } from '../scenarios/events';
import './journey.css';

export function JourneyPanel({ journey, onStart, onClose }: { journey: Journey; onStart: () => void; onClose: () => void }) {
  const { snapshot: s } = journey;
  const latest = s.trace?.adversary?.decisions.at(-1);
  const scenarioLabel = (type: string) => eventTypes().find(event => event.type === type)?.label ?? type;
  return <section className="journey-panel" aria-labelledby="journey-title">
    <header><div><span className="journey-eyebrow">NYC · CONTINUOUS JOURNEY</span><h2 id="journey-title"><Bot size={18} />Driver vs adversary</h2></div>
      <button className="icon-button" onClick={onClose} aria-label="Close journey panel"><X size={18} /></button></header>
    <div className="journey-opening"><strong>Scripted opening · slow passes</strong><p>1. Crash → 2. Stalled vehicle · about 7 mph around each.</p>
      <small>Preset hazards, steering and speed. Afterwards, Carbon picks hazards and Gemini Decisions drives.</small></div>
    <details className="journey-menu"><summary>Available scenarios ({eventTypes().length})</summary><ul>{eventTypes().map(event => <li key={event.type}>{event.label}</li>)}</ul></details>
    <div className="journey-actions">{journey.active
      ? <button className="driver-stop" onClick={journey.stop}><Square size={14} />Stop journey</button>
      : <button className="driver-start" onClick={onStart}><Play size={14} />{s.trip ? 'Start new journey' : 'Start journey'}</button>}
      <button className="driver-retry" disabled={s.windows === 0 || journey.active} onClick={journey.exportReport}><Download size={14} />Export results</button></div>
    <small>The opening makes no model calls. Afterwards, Carbon + Decisions calls continue until stopped or the budget is reached. Hiding this tab stops the session.</small>
    {journey.error && <p className="driver-error" role="alert">{journey.error}</p>}
    {journey.status !== 'idle' && <>
      <div className="journey-status" role="status"><i className={journey.active ? 'active' : ''} />{journey.active
        ? s.phase === 'routing' ? 'Planning the next street route…' : s.phase === 'opening' ? 'Scripted opening in progress' : s.phase === 'thinking' ? 'Carbon is choosing a scenario…' : 'Journey in progress'
        : journey.status === 'error' ? 'Journey interrupted' : 'Journey stopped'}</div>
      <strong className="journey-route">{s.routeLabel || 'Choosing a random NYC start'}</strong>
      <div className="journey-metrics"><div><b>{(s.distanceM / 1000).toFixed(2)}</b><span>km travelled</span></div><div><b>{Math.round(s.speedMps * 2.23694)}</b><span>mph</span></div>
        <div><b>{s.trip}</b><span>trip</span></div><div><b>{s.restarts}</b><span>stuck restarts</span></div></div>
      <div className="journey-agents"><p><b>Driver</b><span>{s.driverAction} · {s.decisions} decisions</span></p>
        <p><b>{s.phase === 'opening' ? 'Sequence' : 'Adversary'}</b><span>{s.phase === 'opening' ? 'Preset crash, then stalled vehicle' : latest ? latest.decision.reason : 'Observing the route'}</span></p></div>
      <small>After 25 simulated seconds with less than 2 m progress, start elsewhere. Carbon thinking pauses follow the adversary’s existing timing rules.</small>
      <h3>Scenarios & outcomes</h3>
      {s.opening && <ol className="journey-events">{s.opening.events.map(event => <li key={event.id}>
        <div><strong>{scenarioLabel(event.type)}</strong><span>{event.rejoinedAt !== null ? 'Passed · back on route' : event.passedAt !== null ? 'Returning to route' : event.appliedAt !== null ? 'Slow pass in progress' : 'Up next'}</span></div>
        <small>Scripted demonstration{event.appliedAt !== null && ` · appeared at ${event.appliedAt.toFixed(1)}s`}{event.rejoinedAt !== null && ` · rejoined at ${event.rejoinedAt.toFixed(1)}s`}</small>
      </li>)}</ol>}
      {s.attacks.length === 0 ? <p className="journey-empty">{s.phase === 'opening' ? 'Live adversary starts after both passes.' : 'Waiting for the adversary’s first scenario.'}</p> : <ol className="journey-events">
        {[...s.attacks].reverse().slice(0, 12).map(row => <li key={`${row.window}-${row.eventId}`}>
          <div><strong>{scenarioLabel(row.eventType)}</strong><span>{row.void === 'not_reached' ? journey.active && row.window === s.windows ? 'Scheduled' : 'Not reached' : row.void === 'unavoidable' ? 'Skipped: too close at trigger speed' : row.void ? 'Skipped: route too short' : row.outcome?.replaceAll('_', ' ')}</span></div>
          <p>{row.reason}</p><small>Window {row.window} · trigger {row.triggerTime.toFixed(1)}s · {row.guardAssisted ? 'Guard assisted' : 'No guard assistance'}{row.resolution && ` · ${row.resolution.replaceAll('_', ' ')}`}</small>
        </li>)}</ol>}
      {s.restartsLog.length > 0 && <details><summary>Restart history ({s.restarts})</summary>{s.restartsLog.slice(-8).reverse().map(row => <p key={row.trip}>Trip {row.trip}: {row.reason}</p>)}</details>}
      <small>Illustrative street simulation; traffic and hazards are synthetic. Results retain the last 80 attacks and 4 completed windows.</small>
    </>}
  </section>;
}
