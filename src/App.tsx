import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Camera, CarFront, Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, CircleStop, Crosshair, Focus, Globe2, Layers3, MapPin, Maximize2, Menu, Minus, Bot, Navigation, Pause, Pin, Play, Radio, RotateCcw, Route, ScanEye, Search, Video, X } from 'lucide-react';
import CityMap from './map/CityMap';
import { demoScenario } from './scene/DemoScenarioProvider';
import { useCameraSources } from './media/cameras';
import { LiveFeed } from './media/LiveFeed';
import { usePlayback } from './hooks/usePlayback';
import { EvidencePanel } from './evidence/EvidencePanel';
import { useGeminiDriver } from './agent/useGeminiDriver';
import { DriverPanel } from './agent/DriverPanel';
import { preset, withEventActors } from './scenarios/events';
import NavigationPanel, { cameraLocation } from './components/NavigationPanel';
import { fetchStreetRoute } from './navigation/routing';
import { createIntersectionScenario, createRouteScenario } from './navigation/routeScenario';
import type { NavigationPoint, PlannedRoute } from './navigation/types';
import type { CameraSource, MapViewport, RenderOptions, ScenarioBundle, ViewMode } from './types';

const MODES: { id: ViewMode; label: string; icon: typeof Globe2 }[] = [
  { id: 'city', label: 'Map', icon: Globe2 },
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
  const [sourceFilter, setSourceFilter] = useState<'all' | 'hls' | 'snapshot'>('all');
  const [boroughFilter, setBoroughFilter] = useState('all');
  const [cameraPage, setCameraPage] = useState(0);
  const [railTab, setRailTab] = useState<'cameras' | 'route' | 'driver'>('cameras');
  const [scenario, setScenario] = useState<ScenarioBundle>(demoScenario);
  const [scenarioPlan, setScenarioPlan] = useState(() => preset());
  const renderedScenario = useMemo(() => scenario.motionSource === 'event-replay' ? scenario : withEventActors(scenario, scenarioPlan), [scenario, scenarioPlan]);
  const [pickedLocation, setPickedLocation] = useState<NavigationPoint | null>(null);
  const [navigationStart, setNavigationStart] = useState<NavigationPoint | null>(null);
  const [navigationEnd, setNavigationEnd] = useState<NavigationPoint | null>(null);
  const [plannedRoute, setPlannedRoute] = useState<PlannedRoute | null>(null);
  const [routeStatus, setRouteStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [routeError, setRouteError] = useState('');
  const [pickingMode, setPickingMode] = useState<'inspect' | 'start' | 'destination'>('inspect');
  const [focusLocation, setFocusLocation] = useState<NavigationPoint | null>(null);
  const [focusRevision, setFocusRevision] = useState(0);
  const routeRequest = useRef<AbortController | null>(null);
  const [selectedActorId, setSelectedActorId] = useState(demoScenario.actors.find(a => a.role === 'ego')!.id);
  const [actorsVisible, setActorsVisible] = useState(true);
  const [routesVisible, setRoutesVisible] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceToggleRef = useRef<HTMLButtonElement>(null);
  const closeEvidence = useCallback(() => { setEvidenceOpen(false); evidenceToggleRef.current?.focus(); }, []);
  const helpRef = useRef<HTMLElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState('');
  const [viewport, setViewport] = useState<MapViewport>({ width: window.innerWidth, height: window.innerHeight, cameraPoint: null });
  const playback = usePlayback(renderedScenario);
  const driver = useGeminiDriver(renderedScenario, scenarioPlan);
  const selectedCamera = cameras.find(c => c.id === selectedCameraId) ?? null;
  const optionsRef = useRef<RenderOptions>({ actorsVisible, routesVisible, selectedActorId });
  optionsRef.current = { actorsVisible, routesVisible, selectedActorId };
  // Keep one runtime identity for the custom map layer while switching its clock.
  const displayedFrame = useRef(playback.frameRef);
  displayedFrame.current = driver.hasRun ? driver.frameRef : playback.frameRef;
  const runtime = useMemo(() => ({ frameRef: { get current() { return displayedFrame.current.current; } }, optionsRef }), []);
  const displayTime = driver.hasRun ? driver.time : playback.time;
  const timelineDuration = driver.hasRun ? 36 : scenario.duration;
  const actor = renderedScenario.actors.find(a => a.id === selectedActorId) ?? renderedScenario.actors[0];
  const actorState = runtime.frameRef.current.actors.find(a => a.id === actor.id);
  const filteredCameras = useMemo(() => cameras.filter(c => (sourceFilter === 'all' || c.kind === sourceFilter)
    && (boroughFilter === 'all' || c.borough === boroughFilter)
    && `${c.name} ${c.borough ?? ''} ${c.description ?? ''}`.toLowerCase().includes(search.trim().toLowerCase())), [cameras, sourceFilter, boroughFilter, search]);
  const pageCount = Math.max(1, Math.ceil(filteredCameras.length / 40));
  const currentPage = Math.min(cameraPage, pageCount - 1);
  const visibleCameras = filteredCameras.slice(currentPage * 40, (currentPage + 1) * 40);
  const liveCount = cameras.filter(c => c.kind === 'hls').length;
  const docked = viewMode !== 'city' || feedPinned || viewport.width < 760;
  const trafficDemoActive = scenario.id === demoScenario.id && viewMode !== 'city';
  const evidenceIsModal = evidenceOpen && viewport.width <= 760;
  const railIsModal = sidebarOpen && viewport.width <= 760 && !evidenceOpen;

  const invalidateRoute = useCallback(() => {
    driver.reset();
    routeRequest.current?.abort();
    routeRequest.current = null;
    setPlannedRoute(null); setRouteStatus('idle'); setRouteError('');
    setScenario(current => current.geometryKind === 'street-route' ? demoScenario : current);
    setViewMode('city');
  }, [driver.reset]);
  const setEndpoint = useCallback((mode: 'start' | 'destination', point: NavigationPoint | null) => {
    invalidateRoute();
    if (mode === 'start') setNavigationStart(point); else setNavigationEnd(point);
    setPickingMode('inspect'); setRailTab('route');
  }, [invalidateRoute]);
  const selectLocation = useCallback((point: NavigationPoint) => {
    if (pickingMode !== 'inspect') {
      setEndpoint(pickingMode, point);
      if (window.innerWidth < 760) setSidebarOpen(true);
    } else { setPickedLocation(point); setFocusLocation(point); }
  }, [pickingMode, setEndpoint]);
  const runScenario = useCallback((next: ScenarioBundle) => {
    driver.reset();
    setScenario(next); setSelectedActorId(next.actors.find(a => a.role === 'ego')?.id ?? next.actors[0].id);
    setViewMode('intersection'); setViewRevision(value => value + 1);
    setPickedLocation(null); setPickingMode('inspect'); setFeedCollapsed(true);
    playback.setPlaying(true);
    if (window.innerWidth < 760) setSidebarOpen(false);
  }, [playback.setPlaying, driver.reset]);
  const startHere = useCallback((point: NavigationPoint) => {
    setEndpoint('start', point); setNavigationEnd(null);
    runScenario(createIntersectionScenario(point, selectedCameraId));
  }, [runScenario, selectedCameraId, setEndpoint]);
  const startTrafficDemo = useCallback(() => {
    routeRequest.current?.abort(); routeRequest.current = null;
    setRouteStatus(current => current === 'loading' ? 'idle' : current); setRouteError('');
    setFocusLocation(null);
    setSelectedCameraId(demoScenario.cameraId);
    setActorsVisible(true); setRoutesVisible(true); setLayersOpen(false);
    setSidebarOpen(false);
    playback.seek(0); playback.setSpeed(1);
    runScenario(demoScenario);
  }, [playback.seek, playback.setSpeed, runScenario]);
  const planRoute = async () => {
    driver.stop();
    if (!navigationStart || !navigationEnd) return;
    routeRequest.current?.abort();
    const controller = new AbortController(); routeRequest.current = controller;
    setRouteStatus('loading'); setRouteError(''); setPlannedRoute(null);
    setFeedCollapsed(true); setPickedLocation(null);
    try {
      const route = await fetchStreetRoute(navigationStart, navigationEnd, controller.signal);
      if (routeRequest.current !== controller || controller.signal.aborted) return;
      setPlannedRoute(route); setRouteStatus('ready'); setViewMode('city'); setPickedLocation(null);
    } catch (error) {
      if (routeRequest.current !== controller || controller.signal.aborted) return;
      setRouteStatus('error'); setRouteError(error instanceof Error ? error.message : 'The route service is unavailable. Please try again.');
    }
  };
  useEffect(() => () => routeRequest.current?.abort(), []);
  useEffect(() => setCameraPage(0), [search, sourceFilter, boroughFilter]);

  const chooseCamera = useCallback((id: string) => {
    driver.stop();
    setSelectedCameraId(id);
    const camera = cameras.find(item => item.id === id);
    if (camera) { setPickedLocation(cameraLocation(camera)); setFocusLocation(cameraLocation(camera)); setFocusRevision(value => value + 1); }
    setViewMode('city');
    setFeedCollapsed(false);
    setFeedStatus('connecting');
    if (window.innerWidth < 760) setSidebarOpen(false);
  }, [cameras, driver.stop]);
  const chooseView = useCallback((mode: ViewMode) => {
    setViewRevision(value => value + 1);
    if (mode !== 'city') {
      setPickedLocation(null);
      if (mode === 'follow') setSelectedActorId(scenario.actors.find(a => a.role === 'ego')!.id);
    }
    setViewMode(mode);
    if (window.innerWidth < 760) setSidebarOpen(false);
  }, [scenario]);
  const startDriver = useCallback((mode: 'baseline' | 'event' = 'event') => {
    playback.setPlaying(false);
    setSelectedActorId(scenario.actors.find(item => item.role === 'ego')!.id);
    setActorsVisible(true); setRoutesVisible(true); setFeedCollapsed(true);
    setPickedLocation(null); setPickingMode('inspect'); setRailTab('driver');
    setViewMode('follow'); setViewRevision(value => value + 1);
    if (window.innerWidth < 760) setSidebarOpen(false);
    driver.start(mode);
  }, [driver.start, playback.setPlaying, scenario]);
  const returnToReplay = () => { driver.reset(); playback.seek(0); playback.setPlaying(false); };
  const exitFollow = useCallback(() => setViewMode(mode => mode === 'follow' ? 'intersection' : mode), []);
  const onMapReady = useCallback(() => setMapReady(true), []);
  const onMapError = useCallback((message: string) => setMapError(message), []);
  const onViewport = useCallback((next: MapViewport) => setViewport(next), []);

  useEffect(() => {
    if (!workspaceRef.current) return;
    const rail = evidenceIsModal ? workspaceRef.current.querySelector<HTMLElement>('#camera-evidence-panel')
      : railIsModal ? railRef.current : null;
    if (!rail) return;
    const opener = evidenceIsModal ? evidenceToggleRef.current
      : document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const covered = [...workspaceRef.current.children].filter((node): node is HTMLElement =>
      node instanceof HTMLElement && node !== rail && !node.classList.contains('sidebar-backdrop'));
    const inertBefore = covered.map(node => node.inert);
    covered.forEach(node => { node.inert = true; });
    rail.focus({ preventScroll: true });
    const containFocus = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (evidenceIsModal) setEvidenceOpen(false); else setSidebarOpen(false);
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = [...rail.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')]
        .filter(node => node.getClientRects().length > 0);
      const first = controls[0], last = controls.at(-1);
      if (!first || !last) { event.preventDefault(); rail.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === rail)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === rail || !rail.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', containFocus, true);
    return () => {
      document.removeEventListener('keydown', containFocus, true);
      covered.forEach((node, index) => { node.inert = inertBefore[index]; });
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [railIsModal, evidenceIsModal]);

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
      if (event.key === 'Escape') { setHelpOpen(false); setLayersOpen(false); setPickedLocation(null); setPickingMode('inspect'); if (evidenceOpen) closeEvidence(); return; }
      if (event.target instanceof HTMLElement && (/INPUT|TEXTAREA|SELECT|BUTTON/.test(event.target.tagName) || event.target.closest('.evidence-panel'))) return;
      if (event.code === 'Space') { event.preventDefault(); if (driver.hasRun) driver.stop(); else playback.setPlaying(p => !p); }
      if (event.key.toLowerCase() === 'c') setFeedsVisible(v => !v);
      if (event.key === '1') chooseView('city');
      if (event.key === '2') chooseView('intersection');
      if (event.key === '3') chooseView('follow');
      if (event.key === '/') { event.preventDefault(); setRailTab('cameras'); setSidebarOpen(true); requestAnimationFrame(() => document.querySelector<HTMLInputElement>('[aria-label="Search cameras"]')?.focus()); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chooseView, playback.setPlaying, driver.hasRun, driver.stop, evidenceOpen, closeEvidence]);

  const cardLeft = Math.max(sidebarOpen && viewport.width > 760 ? 310 : 24, Math.min((viewport.cameraPoint?.x ?? viewport.width * 0.65) + 25, viewport.width - 370));
  const cardTop = Math.max(150, Math.min((viewport.cameraPoint?.y ?? viewport.height * 0.4) - 100, viewport.height - 495));

  return <main ref={workspaceRef} className={`workspace ${sidebarOpen ? 'sidebar-is-open' : ''} ${feedsVisible ? 'feeds-are-visible' : ''}`}>
    <CityMap scenario={renderedScenario} focusLocation={focusLocation} focusRevision={focusRevision} navigationStart={trafficDemoActive ? null : navigationStart} navigationEnd={trafficDemoActive ? null : navigationEnd} plannedRoute={trafficDemoActive ? null : plannedRoute} pickingMode={pickingMode} onLocationSelect={selectLocation} runtime={runtime} viewMode={viewMode} viewRevision={viewRevision} selectedCamera={selectedCamera} cameras={railTab === 'cameras' ? filteredCameras : cameras} sidebarOpen={sidebarOpen} feedsVisible={feedsVisible && docked && !feedCollapsed} onCameraSelect={chooseCamera} onActorSelect={setSelectedActorId} onFollowExit={exitFollow} onViewport={onViewport} onReady={onMapReady} onError={onMapError} />

    <header className="topbar">
      <button className="icon-button sidebar-toggle" onClick={() => setSidebarOpen(v => !v)} aria-label={sidebarOpen ? 'Close workspace tools' : 'Open workspace tools'} title="Workspace tools"><Menu size={19} /></button>
      <a className="brand" href="#" aria-label="Gridlocked city overview" onClick={e => { e.preventDefault(); chooseView('city'); }}>
        <span className="brand-symbol" aria-hidden="true"><i /><i /><i /><i /></span><span>gridlocked<span className="brand-period">.</span></span>
      </a>
      <nav className="view-switch" aria-label="Map perspective">{MODES.map(({ id, label, icon: Icon }) => <button key={id} className={viewMode === id ? 'active' : ''} onClick={() => chooseView(id)} aria-pressed={viewMode === id}><Icon size={15} /><span>{label}</span></button>)}</nav>
      <div className="topbar-actions"><button ref={evidenceToggleRef} className="evidence-toggle" onClick={() => setEvidenceOpen(value => !value)} aria-expanded={evidenceOpen} aria-controls="camera-evidence-panel" aria-label="Camera evidence" title="Capture and inspect camera evidence"><ScanEye size={17} /><span>Evidence</span></button><button className={`driver-launch ${driver.active ? 'is-active' : ''}`} onClick={() => { setRailTab('driver'); setSidebarOpen(true); }} aria-label="Gemini driver"><Bot size={17} /><span>Gemini driver</span>{driver.active && <i />}</button><button className={`traffic-demo-button ${trafficDemoActive ? 'is-active' : ''}`} onClick={startTrafficDemo} aria-label="Traffic demo" aria-pressed={trafficDemoActive} title="Start the Vestry traffic demo: 25 vehicles, signals, and a 60-second loop"><CarFront size={17} /><span className="demo-full-label">Traffic demo</span><span className="demo-short-label">Demo</span></button><button className={`camera-toggle ${feedsVisible ? 'is-active' : ''}`} onClick={() => setFeedsVisible(v => !v)} aria-pressed={feedsVisible} aria-label="Cameras"><Video size={16} /><span>Cameras</span><span className="toggle-track"><i /></span></button><button className="icon-button help-button" aria-label="About this workspace" onClick={() => setHelpOpen(v => !v)}><CircleHelp size={18} /></button></div>
    </header>

    {sidebarOpen && <><button className="sidebar-backdrop" aria-label="Close workspace tools" onClick={() => setSidebarOpen(false)} /><aside ref={railRef} className="source-rail" role={railIsModal ? 'dialog' : undefined} aria-modal={railIsModal || undefined} aria-labelledby="workspace-tools-title" tabIndex={-1}>
      <div className="rail-heading"><div><h1 id="workspace-tools-title">{railTab === 'cameras' ? 'Eyes on New York' : railTab === 'route' ? 'The next move' : 'Let Gemini drive'}</h1><p>{railTab === 'cameras' ? 'Five boroughs. Street by street.' : railTab === 'route' ? 'Choose a start. Find your way.' : 'A model behind the wheel.'}</p></div><button className="icon-button" onClick={() => setSidebarOpen(false)} aria-label="Collapse workspace tools"><ChevronLeft size={17} /></button></div>
      <div className="rail-mode-switch" aria-label="Workspace tools"><button className={railTab === 'cameras' ? 'active' : ''} onClick={() => setRailTab('cameras')} aria-pressed={railTab === 'cameras'}><Camera size={16} />Cameras</button><button className={railTab === 'route' ? 'active' : ''} onClick={() => setRailTab('route')} aria-pressed={railTab === 'route'}><Route size={16} />Route</button><button className={railTab === 'driver' ? 'active' : ''} onClick={() => setRailTab('driver')} aria-pressed={railTab === 'driver'}><Bot size={16} />Driver</button></div>
      {railTab === 'cameras' ? <>
        <label className="search-box"><Search size={16} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Street, avenue, or borough" aria-label="Search cameras" /><kbd>/</kbd></label>
        <div className="camera-filters"><label htmlFor="borough-filter">Borough</label><select id="borough-filter" value={boroughFilter} onChange={event => setBoroughFilter(event.target.value)}><option value="all">All five boroughs</option>{['Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island'].map(borough => <option key={borough}>{borough}</option>)}</select></div>
        <div className="source-tabs">{([{ id: 'all', label: 'All', count: cameras.length }, { id: 'hls', label: 'Video', count: liveCount }, { id: 'snapshot', label: 'Images', count: cameras.length - liveCount }] as const).map(filter => <button key={filter.id} className={sourceFilter === filter.id ? 'active' : ''} onClick={() => setSourceFilter(filter.id)} aria-pressed={sourceFilter === filter.id}>{filter.label}<span>{filter.count}</span></button>)}</div>
        <div className="camera-results-heading" aria-live="polite">{filteredCameras.length.toLocaleString()} cameras{search || boroughFilter !== 'all' ? ' match' : ' in the directory'}</div>
        <div className="camera-list" key={`${sourceFilter}-${boroughFilter}-${currentPage}`}>{visibleCameras.map(camera => <CameraRow key={camera.id} camera={camera} selected={camera.id === selectedCameraId} onSelect={() => chooseCamera(camera.id)} />)}{filteredCameras.length === 0 && <div className="empty-sources"><Camera size={25} /><p>{search ? 'No matching cameras.' : 'No cameras available in this filter.'}</p><span>{search ? 'Try another street or borough.' : sourceFilter === 'hls' ? 'No configured video feeds here. Try All or Images.' : registryStatus === 'connected' ? 'Try All or choose another borough.' : 'The DOT directory may be offline. Try All or Video.'}</span></div>}</div>
        {pageCount > 1 && <div className="camera-pagination"><button className="icon-button" disabled={currentPage === 0} onClick={() => setCameraPage(currentPage - 1)} aria-label="Previous camera page"><ChevronLeft size={17} /></button><span>{currentPage * 40 + 1}–{Math.min((currentPage + 1) * 40, filteredCameras.length)} of {filteredCameras.length}</span><button className="icon-button" disabled={currentPage === pageCount - 1} onClick={() => setCameraPage(currentPage + 1)} aria-label="Next camera page"><ChevronRight size={17} /></button></div>}
        <div className="rail-bottom"><div className="registry-note"><Radio size={13} /><span>{registryStatus === 'connected' ? 'Public camera directory connected' : registryStatus === 'loading' ? 'Loading NYC camera directory…' : registryStatus === 'stale' ? 'Directory partially available · saved cameras retained' : 'Directory offline · saved sources only'}</span></div><button className="explore-button" onClick={() => { setRailTab('route'); if (selectedCamera && !navigationStart) setEndpoint('start', cameraLocation(selectedCamera)); }}><Route size={16} />Plan a drive from here</button><div className="rail-credit">NYC DOT <span>·</span> 511NY / NYSDOT</div></div>
      </> : railTab === 'driver' ? <DriverPanel driver={driver} onStart={startDriver} plan={scenarioPlan} onPlanChange={setScenarioPlan} onReplay={runScenario} /> : <NavigationPanel start={navigationStart} end={navigationEnd} cameras={cameras} pickingMode={pickingMode} route={plannedRoute} status={routeStatus} error={routeError} onStart={point => setEndpoint('start', point)} onEnd={point => setEndpoint('destination', point)} onPick={mode => { driver.stop(); setPickingMode(pickingMode === mode ? 'inspect' : mode); setViewMode('city'); setPickedLocation(null); if (window.innerWidth < 760) setSidebarOpen(false); }} onPlan={() => void planRoute()} onRun={() => { if (plannedRoute && navigationStart) runScenario(createRouteScenario(plannedRoute, navigationStart, selectedCameraId)); }} onClear={() => { invalidateRoute(); setNavigationStart(null); setNavigationEnd(null); setPickingMode('inspect'); }} />}
    </aside></>}

    <div className="scene-heading"><h2>{viewMode === 'city' ? 'New York, in motion.' : scenario.label}</h2><p>{viewMode === 'city' ? 'Find a camera. Choose a street. Take the wheel.' : driver.hasRun ? `Gemini at the wheel · ${driver.status === 'thinking' ? 'deciding the next move' : driver.status === 'driving' ? 'following the route' : driver.status}` : scenario.motionSource === 'event-replay' ? 'Recorded synthetic run · playback only' : scenario.geometryKind === 'street-route' ? scenario.motionSource === 'location-preview' ? 'Location preview · choose a destination to drive' : scenario.motionSource === 'street-preview' ? 'Local preview · direction unverified' : 'Driving route · simulated vehicle motion' : `${demoScenario.actors.length} simulated vehicles · signals · 60-second loop`}</p>{viewMode === 'city' && <button className="text-action" onClick={() => { setRailTab('route'); setSidebarOpen(true); }}>Plan a route <ArrowUpRight size={15} /></button>}</div>

    {pickingMode !== 'inspect' && <div className="map-pick-instruction" role="status"><Crosshair size={17} /><span>Click a street or camera to set your {pickingMode === 'start' ? 'starting point' : 'destination'}.</span><button onClick={() => setPickingMode('inspect')} aria-label="Cancel map selection"><X size={16} /></button></div>}
    {pickedLocation && pickingMode === 'inspect' && <section className="location-card" aria-label="Selected location"><div className="location-card-title"><MapPin size={17} /><div><h3>{pickedLocation.label}</h3></div><button className="icon-button" onClick={() => setPickedLocation(null)} aria-label="Close selected location"><X size={16} /></button></div><div className="location-card-actions"><button className="explore-button" onClick={() => startHere(pickedLocation)}><Play size={14} />Start here</button><button onClick={() => { setEndpoint('start', pickedLocation); setSidebarOpen(true); setPickedLocation(null); }}>Set start</button><button onClick={() => { setEndpoint('destination', pickedLocation); setSidebarOpen(true); setPickedLocation(null); }}>Set destination</button></div><p>{pickedLocation.streetPath ? 'Local street preview. Plan a route for driving directions.' : 'Start a location preview, then choose a destination to drive.'}</p></section>}

    <div className="scene-tools"><div className="layers-anchor"><button className={`tool-pill ${layersOpen ? 'active' : ''}`} onClick={() => setLayersOpen(v => !v)} aria-expanded={layersOpen}><Layers3 size={16} />Layers<ChevronDown size={13} /></button>{layersOpen && <div className="layer-menu"><button aria-pressed={actorsVisible} onClick={() => setActorsVisible(v => !v)}><span><Navigation size={15} />Traffic actors</span>{actorsVisible ? <Check size={16} /> : <span />}</button><button aria-pressed={routesVisible} onClick={() => setRoutesVisible(v => !v)}><span><Route size={15} />Vehicle route</span>{routesVisible ? <Check size={16} /> : <span />}</button><button aria-pressed={feedsVisible} onClick={() => setFeedsVisible(v => !v)}><span><Video size={15} />Camera feeds</span>{feedsVisible ? <Check size={16} /> : <span />}</button></div>}</div></div>

    {viewMode !== 'city' && <section className="actor-hud" aria-label="Selected actor telemetry"><span className="ego-badge"><Navigation size={13} />{actor.role === 'ego' ? 'EGO VEHICLE' : 'TRAFFIC ACTOR'}</span><div className="speed-value">{Math.round((actorState?.speed ?? 0) * 2.236936)}<span>mph</span></div><div className="actor-hud-meta"><span>{driver.hasRun && actor.role === 'ego' ? 'Gemini-controlled ego' : actor.label}</span><span className={scenario.geometryKind === 'street-route' ? 'simulation-assumption' : `signal-badge signal-${runtime.frameRef.current.signal}`}>{scenario.geometryKind === 'street-route' ? 'Simulated speed' : `${runtime.frameRef.current.signal} · demo signal`}</span></div>{viewMode !== 'follow' && <button className="hud-follow" onClick={() => chooseView('follow')}><Crosshair size={13} />Follow ego vehicle</button>}</section>}

    {feedsVisible && selectedCamera && <section className={`feed-surface ${docked ? 'feed-docked' : 'feed-anchored'} ${feedCollapsed ? 'feed-collapsed' : ''}`} style={docked ? undefined : { left: cardLeft, top: cardTop }} aria-label="Selected camera feed">
      <div className="feed-surface-header"><div className="feed-source-label"><Video size={14} /><span>{selectedCamera.kind === 'hls' ? 'SOURCE CAMERA' : 'LATEST IMAGE'}</span><span className={`player-state ${feedStatus === 'playing' ? 'is-playing' : ''}`}>{feedCollapsed ? 'HIDDEN' : feedStatus === 'playing' ? 'LIVE' : feedStatus === 'connecting' ? 'CONNECTING' : feedStatus.toUpperCase()}</span></div><div className="feed-header-actions">{viewMode === 'city' && <button className={`icon-button ${feedPinned ? 'active' : ''}`} onClick={() => setFeedPinned(v => !v)} aria-label={feedPinned ? 'Unpin camera dock' : 'Pin camera to dock'}><Pin size={13} /></button>}<button className="icon-button" onClick={() => setFeedCollapsed(v => !v)} aria-label={feedCollapsed ? 'Expand camera feed' : 'Collapse camera feed'}>{feedCollapsed ? <Maximize2 size={14} /> : <Minus size={15} />}</button><button className="icon-button" onClick={() => setFeedsVisible(false)} aria-label="Hide camera overlay"><X size={15} /></button></div></div>
      {!feedCollapsed && <div className="feed-content"><div className="active-feed"><LiveFeed camera={selectedCamera} onStatus={setFeedStatus} compact={!docked} /></div><div className="feed-information"><div><span className="eyebrow">{selectedCamera.provider}</span><h3>{selectedCamera.name}</h3><p>{selectedCamera.description || `Public roadside camera · ${selectedCamera.borough ?? 'New York City'}`}</p></div><div className="feed-facts"><span><MapPin size={12} />{selectedCamera.coordinates[1].toFixed(4)}° N · {Math.abs(selectedCamera.coordinates[0]).toFixed(4)}° W</span><span>{selectedCamera.resolution || 'Source resolution varies'} <span className="fact-separator">/</span> {selectedCamera.kind === 'hls' ? 'Roadside video' : 'Periodic snapshot'}</span></div><div className="feed-footnote">{selectedCamera.kind === 'hls' ? 'Camera footage runs independently of the simulation.' : 'Snapshots are periodic images, not live video.'}<a href={selectedCamera.sourceUrl} target="_blank" rel="noreferrer" aria-label="Open original camera source"><ArrowUpRight size={14} /></a></div></div></div>}
    </section>}

    <section className={`playback-bar ${driver.hasRun ? 'driver-playback' : ''}`} aria-label="Simulation playback controls">
      <div className="playback-identity"><span className="demo-tag">{driver.hasRun ? <Bot size={17} /> : scenario.id === demoScenario.id ? 'DEMO' : 'SIM'}</span><div><strong>{driver.hasRun ? 'Gemini at the wheel' : scenario.geometryKind === 'street-route' ? 'Selected street journey' : 'Vestry traffic demo'}</strong><span>{driver.hasRun ? `${driver.status === 'thinking' ? 'Waiting for a decision' : driver.status === 'driving' ? 'Driving the route' : driver.status} · ${driver.decisionLog.length}/12 decisions` : scenario.playbackMode === 'once' ? 'Stops at destination' : '25 vehicles · 60-second loop'}</span></div></div>
      <div className="transport-controls"><button className="icon-button reset-button" onClick={() => { if (driver.hasRun) returnToReplay(); else playback.seek(0); }} aria-label={driver.hasRun ? 'Return to replay' : 'Reset simulation'} title={driver.hasRun ? 'Return to replay' : 'Reset simulation'}><RotateCcw size={17} /></button>{driver.hasRun ? <button className="play-button" onClick={driver.stop} disabled={!driver.active} aria-label="Stop Gemini drive"><CircleStop size={19} /></button> : <button className="play-button" onClick={() => playback.setPlaying(v => !v)} aria-label={playback.playing ? 'Pause simulation' : 'Play simulation'}>{playback.playing ? <Pause size={17} fill="currentColor" /> : <Play size={17} fill="currentColor" />}</button>}{!driver.hasRun && <button className="playback-speed" onClick={() => playback.setSpeed(playback.speed === 0.5 ? 1 : playback.speed === 1 ? 2 : 0.5)} aria-label={`Playback speed ${playback.speed} times, click to change`}>{playback.speed.toFixed(1)}×</button>}</div>
      <div className="timeline"><input type="range" min="0" max={timelineDuration} step="0.1" value={displayTime} disabled={driver.hasRun} onChange={event => playback.seek(Number(event.target.value))} aria-label="Simulation timeline" style={{ '--progress': `${displayTime / Math.max(.1, timelineDuration) * 100}%` } as React.CSSProperties} /><div className="timeline-labels"><span>{formatTime(displayTime)}</span><span>{driver.hasRun ? 'MODEL CONTROL' : formatTime(scenario.duration)}</span></div></div>
      {driver.hasRun ? <button className="timeline-view-button" onClick={() => { setRailTab('driver'); setSidebarOpen(true); }} aria-label="View Gemini decisions"><Bot size={18} /></button> : <button className="timeline-view-button" onClick={() => chooseView(viewMode === 'city' ? 'intersection' : 'city')} title={viewMode === 'city' ? 'Go to current simulation' : 'City overview'} aria-label={viewMode === 'city' ? 'Go to current simulation' : 'City overview'}>{viewMode === 'city' ? <Focus size={18} /> : <Globe2 size={18} />}</button>}
    </section>

    <div className="bottom-caption"><span>Simulated motion · camera evidence reviewed separately</span><span>Map context © OpenStreetMap</span></div>
    <EvidencePanel camera={selectedCamera} open={evidenceOpen} modal={evidenceIsModal} onClose={closeEvidence} />
    {!mapReady && !mapError && <div className="map-loading"><span className="loading-orbit" /><span>Opening New York</span></div>}
    {mapError && <div className="map-notice"><span>{mapError}</span><button onClick={() => setMapError('')} aria-label="Dismiss map notice"><X size={14} /></button></div>}
    {helpOpen && <div className="help-backdrop" onClick={() => setHelpOpen(false)}><section ref={helpRef} className="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title" onClick={event => event.stopPropagation()}><button className="icon-button help-close" onClick={() => setHelpOpen(false)} aria-label="Close information"><X size={18} /></button><h2 id="help-title">Welcome to Gridlocked.</h2><p>Explore all five boroughs through public roadway cameras. Choose Traffic demo for the original 25-vehicle Vestry scene, select a street for a local preview, or plan a driving route between two locations.</p><div className="help-item"><Video size={20} /><div><strong>Camera footage is real</strong><p>Video comes from 511NY / NYSDOT. Source delay is unknown. NYC DOT snapshots are periodic images.</p></div></div><div className="help-item"><Route size={20} /><div><strong>The simulation is illustrative</strong><p>Routes use OpenStreetMap driving geometry. Speeds and vehicle motion are simulated, without live traffic or signal timing. The Vestry example is authored. Image analysis is not yet connected to this view.</p></div></div><div className="help-item"><Bot size={20} /><div><strong>Gemini can drive the ego vehicle</strong><p>The model chooses acceleration and braking from simulated state. Route steering and safety bounds are deterministic. Each run is limited to 12 decisions; this is separate from the camera-image navigation pipeline.</p></div></div><div className="shortcut-list"><span><kbd>Space</kbd> Play / pause</span><span><kbd>C</kbd> Cameras</span><span><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> Perspective</span></div><button className="explore-button" onClick={() => setHelpOpen(false)}>Back to the city</button></section></div>}
  </main>;
}

function CameraRow({ camera, selected, onSelect }: { camera: CameraSource; selected: boolean; onSelect: () => void }) {
  return <button className={`camera-row ${selected ? 'selected' : ''}`} onClick={onSelect} aria-pressed={selected}><span className="camera-row-icon">{camera.kind === 'hls' ? <Video size={17} /> : <Camera size={17} />}</span><span className="camera-row-copy"><strong>{camera.name}</strong><span>{camera.borough ?? 'Manhattan'} · {camera.kind === 'hls' ? 'Video' : 'Snapshot'}{camera.availability === 'unavailable' ? ' · Offline' : ''}</span></span><span className="camera-row-end">{selected ? <span className="selection-dot" /> : <ChevronRight size={13} />}</span></button>;
}
