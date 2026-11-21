import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Camera, Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Crosshair, Focus, Globe2, Layers3, MapPin, Maximize2, Menu, Minus, Navigation, Pause, Pin, Play, Radio, RotateCcw, Route, ScanEye, Search, Video, X } from 'lucide-react';
import CityMap from './map/CityMap';
import { demoScenario } from './scene/DemoScenarioProvider';
import { useCameraSources } from './media/cameras';
import { LiveFeed } from './media/LiveFeed';
import { usePlayback } from './hooks/usePlayback';
import { EvidencePanel } from './evidence/EvidencePanel';
import type { CameraSource, MapViewport, RenderOptions, ViewMode } from './types';

const MODES: { id: ViewMode; label: string; icon: typeof Globe2 }[] = [
  { id: 'city', label: 'City', icon: Globe2 },
  { id: 'intersection', label: 'Intersection', icon: Focus },
  { id: 'follow', label: 'Follow', icon: Navigation },
];
const formatTime = (time: number) => `${Math.floor(time / 60).toString().padStart(2, '0')}:${Math.floor(time % 60).toString().padStart(2, '0')}`;

export default function App() {
  const { cameras, status: registryStatus } = useCameraSources();
  const [viewMode, setViewMode] = useState<ViewMode>('city');
  const [viewRevision, setViewRevision] = useState(0);
  const [selectedCameraId, setSelectedCameraId] = useState(demoScenario.cameraId);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 760);
  const [feedsVisible, setFeedsVisible] = useState(true);
  const [feedCollapsed, setFeedCollapsed] = useState(false);
  const [feedPinned, setFeedPinned] = useState(false);
  const [feedStatus, setFeedStatus] = useState('connecting');
  const [search, setSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState<'hls' | 'snapshot'>('hls');
  const [selectedActorId, setSelectedActorId] = useState(demoScenario.actors.find(a => a.role === 'ego')!.id);
  const [actorsVisible, setActorsVisible] = useState(true);
  const [routesVisible, setRoutesVisible] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceToggleRef = useRef<HTMLButtonElement>(null);
  const closeEvidence = useCallback(() => { setEvidenceOpen(false); evidenceToggleRef.current?.focus(); }, []);
  const helpRef = useRef<HTMLElement>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState('');
  const [viewport, setViewport] = useState<MapViewport>({ width: window.innerWidth, height: window.innerHeight, cameraPoint: null });
  const playback = usePlayback(demoScenario);
  const selectedCamera = cameras.find(c => c.id === selectedCameraId) ?? null;
  const optionsRef = useRef<RenderOptions>({ actorsVisible, routesVisible, selectedActorId });
  optionsRef.current = { actorsVisible, routesVisible, selectedActorId };
  const runtime = useMemo(() => ({ frameRef: playback.frameRef, optionsRef }), [playback.frameRef]);
  const actor = demoScenario.actors.find(a => a.id === selectedActorId) ?? demoScenario.actors[0];
  const actorState = playback.frameRef.current.actors.find(a => a.id === actor.id);
  const filteredCameras = cameras.filter(c => c.kind === sourceFilter && `${c.name} ${c.description ?? ''}`.toLowerCase().includes(search.toLowerCase())).slice(0, 80);
  const liveCount = cameras.filter(c => c.kind === 'hls').length;
  const docked = viewMode !== 'city' || feedPinned || viewport.width < 760;

  const chooseCamera = useCallback((id: string) => {
    setSelectedCameraId(id);
    setViewMode('city');
    setFeedCollapsed(false);
    setFeedStatus('connecting');
    if (window.innerWidth < 760) setSidebarOpen(false);
  }, []);
  const chooseView = useCallback((mode: ViewMode) => {
    setViewRevision(value => value + 1);
    if (mode !== 'city') {
      setSelectedCameraId(demoScenario.cameraId);
      setFeedCollapsed(false);
      if (mode === 'follow') setSelectedActorId(demoScenario.actors.find(a => a.role === 'ego')!.id);
    }
    setViewMode(mode);
    if (window.innerWidth < 760) setSidebarOpen(false);
  }, []);
  const exitFollow = useCallback(() => setViewMode(mode => mode === 'follow' ? 'intersection' : mode), []);
  const onMapReady = useCallback(() => setMapReady(true), []);
  const onMapError = useCallback((message: string) => setMapError(message), []);
  const onViewport = useCallback((next: MapViewport) => setViewport(next), []);

  useEffect(() => {
    if (!helpOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = helpRef.current;
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !dialog) return;
      const controls = [...dialog.querySelectorAll<HTMLElement>('button,a[href],input,[tabindex="0"]')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', trap);
    return () => { document.removeEventListener('keydown', trap); previous?.focus(); };
  }, [helpOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setHelpOpen(false); setLayersOpen(false); if (evidenceOpen) closeEvidence(); return; }
      if (event.target instanceof HTMLElement && (/INPUT|TEXTAREA|SELECT|BUTTON/.test(event.target.tagName) || event.target.closest('.evidence-panel'))) return;
      if (event.code === 'Space') { event.preventDefault(); playback.setPlaying(p => !p); }
      if (event.key.toLowerCase() === 'c') setFeedsVisible(v => !v);
      if (event.key === '1') chooseView('city');
      if (event.key === '2') chooseView('intersection');
      if (event.key === '3') chooseView('follow');
      if (event.key === '/') { event.preventDefault(); setSidebarOpen(true); requestAnimationFrame(() => document.querySelector<HTMLInputElement>('[aria-label="Search cameras"]')?.focus()); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chooseView, playback.setPlaying, evidenceOpen, closeEvidence]);

  const cardLeft = Math.max(sidebarOpen && viewport.width > 760 ? 310 : 24, Math.min((viewport.cameraPoint?.x ?? viewport.width * 0.65) + 25, viewport.width - 370));
  const cardTop = Math.max(150, Math.min((viewport.cameraPoint?.y ?? viewport.height * 0.4) - 100, viewport.height - 495));

  return <main className={`workspace ${sidebarOpen ? 'sidebar-is-open' : ''} ${feedsVisible ? 'feeds-are-visible' : ''}`}>
    <CityMap scenario={demoScenario} runtime={runtime} viewMode={viewMode} viewRevision={viewRevision} selectedCamera={selectedCamera} cameras={cameras} sidebarOpen={sidebarOpen} feedsVisible={feedsVisible && docked && !feedCollapsed} onCameraSelect={chooseCamera} onActorSelect={setSelectedActorId} onFollowExit={exitFollow} onViewport={onViewport} onReady={onMapReady} onError={onMapError} />

    <header className="topbar">
      <button className="icon-button sidebar-toggle" onClick={() => setSidebarOpen(v => !v)} aria-label={sidebarOpen ? 'Close camera list' : 'Open camera list'} title="Camera list"><Menu size={19} /></button>
      <a className="brand" href="#" aria-label="Gotham Forge city overview" onClick={e => { e.preventDefault(); chooseView('city'); }}>
        <span className="brand-symbol"><i /><i /><i /></span><span>GOTHAM<span className="brand-secondary">FORGE</span></span>
      </a>
      <span className="topbar-divider" />
      <span className="workspace-name">Simulation workspace</span>
      <nav className="view-switch" aria-label="Map perspective">{MODES.map(({ id, label, icon: Icon }) => <button key={id} className={viewMode === id ? 'active' : ''} onClick={() => chooseView(id)} aria-pressed={viewMode === id}><Icon size={15} /><span>{label}</span></button>)}</nav>
      <div className="topbar-actions"><button ref={evidenceToggleRef} className="evidence-toggle" onClick={() => setEvidenceOpen(value => !value)} aria-expanded={evidenceOpen} aria-controls="camera-evidence-panel" aria-label="Camera evidence" title="Capture and inspect camera evidence"><ScanEye size={17} /><span>Evidence</span></button><button className={`camera-toggle ${feedsVisible ? 'is-active' : ''}`} onClick={() => setFeedsVisible(v => !v)} aria-pressed={feedsVisible} aria-label="Cameras"><Video size={16} /><span>Cameras</span><span className="toggle-track"><i /></span></button><button className="icon-button help-button" aria-label="About this workspace" onClick={() => setHelpOpen(v => !v)}><CircleHelp size={18} /></button></div>
    </header>

    {sidebarOpen && <><button className="sidebar-backdrop" aria-label="Close camera list" onClick={() => setSidebarOpen(false)} /><aside className="source-rail">
      <div className="rail-heading"><div><span className="eyebrow">NEW YORK CITY</span><h1>Observation network</h1></div><button className="icon-button" onClick={() => setSidebarOpen(false)} aria-label="Collapse camera list"><ChevronLeft size={17} /></button></div>
      <label className="search-box"><Search size={16} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Find a camera" aria-label="Search cameras" /><kbd>/</kbd></label>
      <div className="source-tabs"><button className={sourceFilter === 'hls' ? 'active' : ''} onClick={() => setSourceFilter('hls')}>Video <span>{liveCount}</span></button><button className={sourceFilter === 'snapshot' ? 'active' : ''} onClick={() => setSourceFilter('snapshot')}>Snapshots <span>{cameras.filter(c => c.kind === 'snapshot').length}</span></button></div>
      <div className="camera-list">{filteredCameras.map(camera => <CameraRow key={camera.id} camera={camera} selected={camera.id === selectedCameraId} onSelect={() => chooseCamera(camera.id)} />)}{filteredCameras.length === 0 && <div className="empty-sources"><Camera size={25} /><p>{search ? 'No matching cameras.' : 'No snapshots are available yet.'}</p><span>{search ? 'Try a street or avenue name.' : 'Video sources remain available in the Video tab.'}</span></div>}</div>
      <div className="rail-bottom"><div className="registry-note"><Radio size={13} /><span>{registryStatus === 'connected' ? 'Source directory connected' : registryStatus === 'loading' ? 'Connecting to source directory' : 'Saved source directory'}</span></div><div className="demo-entry"><span className="eyebrow">EXPLORE A SCENARIO</span><div className="demo-entry-title"><span className="demo-icon"><Route size={22} /></span><div><strong>Vestry intersection</strong><span>Illustrative traffic simulation</span></div></div><button className="explore-button" onClick={() => chooseView('intersection')}>Open simulation <Maximize2 size={15} /></button></div><div className="rail-credit">NYC DOT <span>·</span> 511NY / NYSDOT</div></div>
    </aside></>}

    <div className="scene-heading"><div className="scene-kicker"><span className="location-square" />{viewMode === 'city' ? 'CITY OVERVIEW' : viewMode === 'follow' ? 'VEHICLE PERSPECTIVE' : 'INTERSECTION VIEW'}</div><h2>{viewMode === 'city' ? selectedCameraId === demoScenario.cameraId ? 'Lower Manhattan' : selectedCamera?.name.replace('West Street at ', '') ?? 'New York City' : 'West Street & Vestry'}</h2><p>{viewMode === 'city' ? 'A window into the city, one intersection at a time.' : 'Vestry demo · illustrative traffic'}</p>{viewMode === 'city' && <button className="text-action" onClick={() => chooseView('intersection')}>Enter the simulation <ChevronRight size={15} /></button>}</div>

    <div className="scene-tools"><div className="layers-anchor"><button className={`tool-pill ${layersOpen ? 'active' : ''}`} onClick={() => setLayersOpen(v => !v)} aria-expanded={layersOpen}><Layers3 size={16} />Layers<ChevronDown size={13} /></button>{layersOpen && <div className="layer-menu"><span className="eyebrow">SIMULATION LAYERS</span><button aria-pressed={actorsVisible} onClick={() => setActorsVisible(v => !v)}><span><Navigation size={15} />Traffic actors</span>{actorsVisible ? <Check size={16} /> : <span />}</button><button aria-pressed={routesVisible} onClick={() => setRoutesVisible(v => !v)}><span><Route size={15} />Vehicle route</span>{routesVisible ? <Check size={16} /> : <span />}</button><button aria-pressed={feedsVisible} onClick={() => setFeedsVisible(v => !v)}><span><Video size={15} />Camera feeds</span>{feedsVisible ? <Check size={16} /> : <span />}</button></div>}</div></div>

    {viewMode !== 'city' && <section className="actor-hud" aria-label="Selected actor telemetry"><span className="ego-badge"><Navigation size={13} />{actor.role === 'ego' ? 'EGO VEHICLE' : 'TRAFFIC ACTOR'}</span><div className="speed-value">{Math.round((actorState?.speed ?? 0) * 2.236936)}<span>mph</span></div><div className="actor-hud-meta"><span>{actor.label}</span><span className={`signal-badge signal-${playback.frameRef.current.signal}`}>{playback.frameRef.current.signal}</span></div>{viewMode !== 'follow' && <button className="hud-follow" onClick={() => chooseView('follow')}><Crosshair size={13} />Follow ego vehicle</button>}</section>}

    {feedsVisible && selectedCamera && <section className={`feed-surface ${docked ? 'feed-docked' : 'feed-anchored'} ${feedCollapsed ? 'feed-collapsed' : ''}`} style={docked ? undefined : { left: cardLeft, top: cardTop }} aria-label="Selected camera feed">
      <div className="feed-surface-header"><div className="feed-source-label"><Video size={14} /><span>{selectedCamera.kind === 'hls' ? 'SOURCE CAMERA' : 'LATEST IMAGE'}</span><span className={`player-state ${feedStatus === 'playing' ? 'is-playing' : ''}`}>{feedCollapsed ? 'HIDDEN' : feedStatus === 'playing' ? 'LIVE' : feedStatus === 'connecting' ? 'CONNECTING' : feedStatus.toUpperCase()}</span></div><div className="feed-header-actions">{viewMode === 'city' && <button className={`icon-button ${feedPinned ? 'active' : ''}`} onClick={() => setFeedPinned(v => !v)} aria-label={feedPinned ? 'Unpin camera dock' : 'Pin camera to dock'}><Pin size={13} /></button>}<button className="icon-button" onClick={() => setFeedCollapsed(v => !v)} aria-label={feedCollapsed ? 'Expand camera feed' : 'Collapse camera feed'}>{feedCollapsed ? <Maximize2 size={14} /> : <Minus size={15} />}</button><button className="icon-button" onClick={() => setFeedsVisible(false)} aria-label="Hide camera overlay"><X size={15} /></button></div></div>
      {!feedCollapsed && <div className="feed-content"><div className="active-feed"><LiveFeed camera={selectedCamera} onStatus={setFeedStatus} compact={!docked} /></div><div className="feed-information"><div><span className="eyebrow">{selectedCamera.provider}</span><h3>{selectedCamera.name}</h3><p>{selectedCamera.description || 'Public roadside camera · Manhattan, New York'}</p></div><div className="feed-facts"><span><MapPin size={12} />{selectedCamera.coordinates[1].toFixed(4)}° N · {Math.abs(selectedCamera.coordinates[0]).toFixed(4)}° W</span><span>{selectedCamera.resolution || 'Source resolution varies'} <span className="fact-separator">/</span> {selectedCamera.kind === 'hls' ? 'Roadside video' : 'Periodic snapshot'}</span></div><div className="feed-footnote">{selectedCamera.kind === 'hls' ? 'Live footage runs independently of the demo.' : 'Snapshots are periodic images, not live video.'}<a href={selectedCamera.sourceUrl} target="_blank" rel="noreferrer" aria-label="Open original camera source"><ArrowUpRight size={14} /></a></div></div></div>}
    </section>}

    <section className="playback-bar" aria-label="Demo playback controls"><div className="playback-identity"><span className="demo-tag">DEMO</span><div><strong>Vestry traffic loop</strong><span>Illustrative traffic · seed {demoScenario.seed}</span></div></div><div className="transport-controls"><button className="icon-button reset-button" onClick={() => playback.seek(0)} aria-label="Reset simulation"><RotateCcw size={16} /></button><button className="play-button" onClick={() => playback.setPlaying(v => !v)} aria-label={playback.playing ? 'Pause simulation' : 'Play simulation'}>{playback.playing ? <Pause size={17} fill="currentColor" /> : <Play size={17} fill="currentColor" />}</button><button className="playback-speed" onClick={() => playback.setSpeed(playback.speed === 0.5 ? 1 : playback.speed === 1 ? 2 : 0.5)} aria-label={`Playback speed ${playback.speed} times, click to change`}>{playback.speed.toFixed(1)}×</button></div><div className="timeline"><input type="range" min="0" max={demoScenario.duration} step="0.1" value={playback.time} onChange={event => playback.seek(Number(event.target.value))} aria-label="Simulation timeline" style={{ '--progress': `${playback.time / demoScenario.duration * 100}%` } as React.CSSProperties} /><div className="timeline-labels"><span>{formatTime(playback.time)}</span><span>{formatTime(demoScenario.duration)}</span></div></div><button className="timeline-view-button" onClick={() => chooseView(viewMode === 'city' ? 'intersection' : 'city')} title={viewMode === 'city' ? 'Go to demo' : 'City overview'} aria-label={viewMode === 'city' ? 'Go to demo' : 'City overview'}>{viewMode === 'city' ? <Focus size={18} /> : <Globe2 size={18} />}</button></section>

    <div className="bottom-caption"><span>Vestry demo · illustrative traffic</span><span>Map context © OpenStreetMap</span></div>
    <EvidencePanel camera={selectedCamera} open={evidenceOpen} onClose={closeEvidence} />
    {!mapReady && !mapError && <div className="map-loading"><span className="loading-orbit" /><span>Opening New York</span></div>}
    {mapError && <div className="map-notice"><span>{mapError}</span><button onClick={() => setMapError('')} aria-label="Dismiss map notice"><X size={14} /></button></div>}
    {helpOpen && <div className="help-backdrop" onClick={() => setHelpOpen(false)}><section ref={helpRef} className="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title" onClick={event => event.stopPropagation()}><button className="icon-button help-close" onClick={() => setHelpOpen(false)} aria-label="Close information"><X size={18} /></button><span className="eyebrow">ABOUT THIS WORKSPACE</span><h2 id="help-title">The city. A possible world.</h2><p>Explore New York through public roadside cameras and an authored Vestry Street traffic simulation.</p><div className="help-item"><Video size={20} /><div><strong>Camera footage is real</strong><p>Video comes from 511NY / NYSDOT. Source delay is unknown. NYC DOT snapshots are periodic images.</p></div></div><div className="help-item"><Route size={20} /><div><strong>The simulation is illustrative</strong><p>Road details, signal timings, and traffic are hand-authored. This demo has not been reconstructed from the selected video.</p></div></div><div className="shortcut-list"><span><kbd>Space</kbd> Play / pause</span><span><kbd>C</kbd> Cameras</span><span><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> Perspective</span></div><button className="explore-button" onClick={() => setHelpOpen(false)}>Back to the city</button></section></div>}
  </main>;
}

function CameraRow({ camera, selected, onSelect }: { camera: CameraSource; selected: boolean; onSelect: () => void }) {
  return <button className={`camera-row ${selected ? 'selected' : ''}`} onClick={onSelect} aria-pressed={selected}><span className="camera-row-icon">{camera.kind === 'hls' ? <Video size={17} /> : <Camera size={17} />}</span><span className="camera-row-copy"><strong>{camera.name}</strong><span>{camera.kind === 'hls' ? '511NY roadside video' : 'NYC DOT snapshot'}</span></span><span className="camera-row-end">{selected ? <span className="selection-dot" /> : <ChevronRight size={13} />}</span></button>;
}
