import { useState } from 'react';
import { Crosshair, MapPin, Navigation, Play, Route, X } from 'lucide-react';
import type { CameraSource } from '../types';
import type { NavigationPoint, PlannedRoute } from '../navigation/types';

export function cameraLocation(camera: CameraSource): NavigationPoint {
  return { coordinates: camera.coordinates, label: camera.name };
}

function Endpoint({ label, point, cameras, onChange, onPick, picking }: {
  label: string; point: NavigationPoint | null; cameras: CameraSource[];
  onChange(point: NavigationPoint | null): void; onPick(): void; picking: boolean;
}) {
  const [query, setQuery] = useState('');
  const matches = query.trim().length > 1 ? cameras.filter(camera => `${camera.name} ${camera.borough ?? ''}`.toLowerCase().includes(query.toLowerCase())).slice(0, 6) : [];
  return <div className="route-endpoint">
    <div className="endpoint-label"><span>{label}</span><button onClick={onPick} className={picking ? 'is-picking' : ''} aria-pressed={picking}><Crosshair size={14} />{picking ? 'Click the map…' : 'Pick on map'}</button></div>
    {point ? <div className="endpoint-value"><MapPin size={15} /><span>{point.label}</span><button className="icon-button" onClick={() => onChange(null)} aria-label={`Clear ${label.toLowerCase()}`}><X size={14} /></button></div>
      : <input aria-label={`Search ${label.toLowerCase()} cameras`} value={query} onChange={event => setQuery(event.target.value)} placeholder="Search a camera or pick a street" />}
    {!point && matches.length > 0 && <div className="endpoint-results">{matches.map(camera => <button key={camera.id} onClick={() => { onChange(cameraLocation(camera)); setQuery(''); }}>{camera.name}<span>{camera.borough ?? camera.provider}</span></button>)}</div>}
  </div>;
}

export default function NavigationPanel({ start, end, cameras, pickingMode, route, status, error, onStart, onEnd, onPick, onPlan, onRun, onClear }: {
  start: NavigationPoint | null; end: NavigationPoint | null; cameras: CameraSource[];
  pickingMode: 'inspect' | 'start' | 'destination'; route: PlannedRoute | null;
  status: 'idle' | 'loading' | 'ready' | 'error'; error: string;
  onStart(point: NavigationPoint | null): void; onEnd(point: NavigationPoint | null): void;
  onPick(mode: 'start' | 'destination'): void; onPlan(): void; onRun(): void; onClear(): void;
}) {
  return <section className="navigation-panel" aria-label="Route planner">
    <div className="planner-intro"><Route size={21} /><h2>Choose your next drive</h2><p>Pick a street on the map, or search the camera network for a starting point and destination.</p></div>
    <Endpoint label="Start" point={start} cameras={cameras} onChange={onStart} onPick={() => onPick('start')} picking={pickingMode === 'start'} />
    <Endpoint label="Destination" point={end} cameras={cameras} onChange={onEnd} onPick={() => onPick('destination')} picking={pickingMode === 'destination'} />
    <button className="explore-button plan-button" onClick={onPlan} disabled={!start || !end || status === 'loading'}><Route size={16} />{status === 'loading' ? 'Finding a driving route…' : 'Find driving route'}</button>
    <div aria-live="polite">{error && <p className="route-error">{error}</p>}</div>
    {route && <div className="route-summary">
      <div className="route-metrics"><div><strong>{(route.distanceMeters / 1609.344).toFixed(2)}<span>mi</span></strong><span>Driving distance</span></div><div><strong>{Math.max(1, Math.round(route.durationSeconds / 60))}<span>min</span></strong><span>Routing estimate</span></div></div>
      <button className="explore-button" onClick={onRun}><Play size={16} />Drive this route</button>
      <p className="model-note">Street geometry follows OpenStreetMap routing. Vehicle speed is simulated; live traffic and signal timing are not yet available.</p>
      <p className="route-attribution"><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a> · <a href="https://project-osrm.org/" target="_blank" rel="noreferrer">OSRM routing</a></p>
      <details className="route-directions"><summary>Route directions · {route.steps.length} steps</summary><ol>{route.steps.map((step, index) => <li key={index}><span>{step.label}</span><small>{Math.round(step.distanceMeters)} m</small></li>)}</ol></details>
    </div>}
    <div className="planner-footer"><Navigation size={16} /><p>For a quick local simulation, click a street and choose <strong>Start here</strong>.</p></div>
    {(start || end) && <button className="clear-route" onClick={onClear}>Clear route</button>}
  </section>;
}
