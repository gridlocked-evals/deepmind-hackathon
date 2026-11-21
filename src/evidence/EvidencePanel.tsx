import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ArrowUpRight, Camera, Check, FileText, LockKeyhole, Pause, Play, RefreshCw, ScanEye, Video, X } from 'lucide-react';
import type { CameraSource } from '../types';
import { apiUrl } from './api';
import { EvidenceController, validInterval } from './controller';
import { CapturedMedia } from './CapturedMedia';
import type { ReviewPoint } from './CapturedMedia';
import './evidence.css';

export const formatEvidenceTime = (value: number) => `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toFixed(3).padStart(6, '0')}`;
const receivedTime = (value: string) => new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });

export function EvidencePanel({ camera, open, onClose }: { camera: CameraSource | null; open: boolean; onClose: () => void }) {
  const controller = useMemo(() => new EvidenceController(), []);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const heading = useRef<HTMLHeadingElement>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [review, setReview] = useState<ReviewPoint | null>(null);
  useEffect(() => { controller.select(camera); }, [controller, camera?.id]);
  useEffect(() => {
    if (!open || !camera || camera.kind !== 'hls') { controller.cancelHistory(); return; }
    const refresh = () => { if (!document.hidden) void controller.refresh(); };
    refresh();
    const timer = window.setInterval(() => { if (controller.getSnapshot().history?.recording) refresh(); }, 2500);
    document.addEventListener('visibilitychange', refresh);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', refresh); controller.cancelHistory(); };
  }, [controller, camera?.id, camera?.kind, open]);
  useEffect(() => { if (open) heading.current?.focus(); }, [open]);
  useEffect(() => { setReview(null); setShowOriginal(false); }, [state.capture?.captureId, camera?.id]);
  if (!open) return null;
  const current = state.sourceId === camera?.id;
  const history = current ? state.history : null;
  const capture = current ? state.capture : null;
  const observation = current ? state.observation : null;
  const busy = current ? state.busy : null;
  const duration = state.selection ? state.selection[1] - state.selection[0] : 0;
  const ready = current && validInterval(history, state.selection);
  const phase = history?.recording ? history.state === 'connected' ? 'Recording' : 'Waiting for source' : history?.state === 'stale' ? 'Buffered media is stale' : history?.state === 'unavailable' ? 'Source unavailable' : history?.segmentBoundaries.length ? 'Recording stopped' : 'Not recording';
  return <aside id="camera-evidence-panel" className="evidence-panel" aria-labelledby="evidence-heading" aria-label="Camera evidence workspace">
    <header className="evidence-panel-header"><div><h2 ref={heading} tabIndex={-1} id="evidence-heading">Camera evidence</h2><p>Capture a moment. Inspect what is visible.</p></div><button className="evidence-icon-button" onClick={onClose} aria-label="Close evidence panel"><X size={19} /></button></header>
    <div className="evidence-panel-body">
      {!camera ? <p className="evidence-empty">Select a camera from the map or source list to begin.</p> : <>
        <section className="evidence-source" aria-label="Selected evidence source"><div className="evidence-source-icon">{camera.kind === 'hls' ? <Video size={20} /> : <Camera size={20} />}</div><div><h3>{camera.name}</h3><p>{camera.provider} · {camera.kind === 'hls' ? 'Continuous video' : 'Periodic snapshot'}</p></div><a href={camera.sourceUrl} target="_blank" rel="noreferrer" className="evidence-icon-button" aria-label="Open selected camera source"><ArrowUpRight size={16} /></a></section>

        {current && state.error && <div className="evidence-error" role="alert"><div><strong>Request not completed</strong><p>{state.error.message}</p>{state.error.code && <details className="evidence-error-details"><summary>Error details</summary><code>{state.error.code}</code></details>}{state.error.attemptId && <a href={apiUrl(`/api/observations/attempts/${encodeURIComponent(state.error.attemptId)}`)} target="_blank" rel="noreferrer">View retained attempt <ArrowUpRight size={12} /></a>}</div><button className="evidence-icon-button" aria-label="Dismiss evidence error" onClick={() => controller.dismissError()}><X size={15} /></button></div>}

        <section className="evidence-section" aria-labelledby="capture-heading"><div className="evidence-section-heading"><h3 id="capture-heading">Source capture</h3>{camera.kind === 'hls' && <span className={`evidence-state ${history?.recording && history.state === 'connected' ? 'recording' : ''}`} role="status">{phase}</span>}</div>
          {camera.kind === 'hls' ? <>
            <p className="evidence-description">Save only media actually buffered from this camera. Source delay and exposure time are unknown.</p>
            <div className="evidence-actions"><button className="evidence-button primary" disabled={!!busy || !current || state.historyLoading} onClick={() => void (history?.recording ? controller.stop() : controller.start())}>{history?.recording ? <Pause size={15} /> : <Play size={15} />}{busy === 'starting' ? 'Starting…' : busy === 'stopping' ? 'Stopping…' : history?.recording ? 'Stop recording' : history?.state === 'unavailable' ? 'Restart recording' : 'Start recording'}</button><button className="evidence-button subtle" disabled={!!busy || state.historyLoading} onClick={() => { controller.dismissError(); void controller.refresh(); }}><RefreshCw size={14} />{state.historyLoading ? 'Checking…' : 'Refresh history'}</button></div>
            {history && history.segmentBoundaries.length >= 2 ? <div className="evidence-interval">
              <div className="evidence-interval-top"><span>Buffered interval</span><button onClick={() => controller.chooseLatest()} disabled={!!busy}>Use latest</button></div>
              <div className="evidence-time-fields"><label>Start<select aria-label="Capture interval start" disabled={!!busy} value={state.selection?.[0] ?? ''} onChange={event => controller.choose(Number(event.target.value), state.selection?.[1] ?? 0)}>{history.segmentBoundaries.slice(0, -1).map(value => <option value={value} key={value}>{formatEvidenceTime(value)}</option>)}</select></label><span aria-hidden="true">→</span><label>End<select aria-label="Capture interval end" disabled={!!busy} value={state.selection?.[1] ?? ''} onChange={event => controller.choose(state.selection?.[0] ?? 0, Number(event.target.value))}>{history.segmentBoundaries.slice(1).map(value => <option value={value} key={value}>{formatEvidenceTime(value)}</option>)}</select></label></div>
              <p className={ready ? 'evidence-caption' : 'evidence-inline-error'}>{ready ? `${duration.toFixed(3)} seconds selected · maximum 120 seconds` : 'Choose an end after the start, with no more than 120 seconds between them.'}</p>
              <p className="evidence-caption">Available {formatEvidenceTime(history.earliestSeconds!)}–{formatEvidenceTime(history.latestSeconds!)} on this recording’s clock.</p>
              <button className="evidence-button secondary wide" disabled={!ready || !!busy} onClick={() => void controller.seal()}><LockKeyhole size={15} />{busy === 'sealing' ? 'Saving exact source bytes…' : 'Save selected evidence'}</button>
            </div> : <div className="evidence-empty"><p>{history?.recording ? 'Waiting for the first complete segment.' : 'No recorded history is available for this source.'}</p><span>{history?.recording ? 'Selectable boundaries appear after complete media passes its checks.' : 'Start recording before choosing a past interval.'}</span></div>}
          </> : <>
            <p className="evidence-description">Retain the current periodic image and its receipt time. A single snapshot cannot establish motion.</p><button className="evidence-button primary" disabled={!!busy || !current} onClick={() => void controller.snapshot()}><Camera size={15} />{busy === 'snapshot' ? 'Capturing image…' : 'Capture snapshot'}</button>
          </>}
        </section>

        <section className="evidence-section" aria-labelledby="retained-heading"><div className="evidence-section-heading"><h3 id="retained-heading">Retained evidence</h3>{capture && <span className="evidence-state"><Check size={12} />Saved</span>}</div>
          {capture ? <><div className="evidence-capture-summary"><strong>{capture.modality === 'snapshot' ? 'One original snapshot' : `${capture.durationSeconds.toFixed(3)} seconds of original video`}</strong><p>Received {receivedTime(capture.receivedAt)}</p>{capture.cadence?.repeated && <p className="evidence-notice">This image matches the previous snapshot. It does not establish a fresh exposure.</p>}</div>
            <button className="evidence-button subtle" aria-expanded={showOriginal} onClick={() => setShowOriginal(value => !value)}><ScanEye size={15} />{showOriginal ? 'Hide original media' : 'Review original media'}</button>
            {showOriginal && <CapturedMedia key={capture.captureId} capture={capture} review={review} />}
            <details className="evidence-provenance"><summary><FileText size={14} />Capture provenance</summary><dl><dt>Capture</dt><dd>{capture.captureId}</dd><dt>Actual source</dt><dd>{capture.sourceId}</dd><dt>Exposure clock</dt><dd>{capture.sourceClock === 'unknown' ? 'Unknown' : `${capture.sourceClock} · uncertainty unknown`}</dd><dt>Original SHA-256</dt><dd>{capture.originalMediaSha256}</dd><dt>Frames / bytes</dt><dd>{capture.frameCount.toLocaleString()} / {capture.byteLength.toLocaleString()}</dd></dl><a href={apiUrl(`/api/captures/episodes/${encodeURIComponent(capture.captureId)}`)} target="_blank" rel="noreferrer">Open immutable manifest <ArrowUpRight size={13} /></a></details>
          </> : <p className="evidence-empty">Save a clip or snapshot to preserve the exact input for review and model retries.</p>}
        </section>

        <section className="evidence-section" aria-labelledby="observation-heading"><div className="evidence-section-heading"><h3 id="observation-heading">Hydrogen observations</h3><span className="evidence-paid-label">Paid model call</span></div>
          <p className="evidence-description">Extract visible anchors, counts, signals, and unknowns from the saved source. Each run uses the same retained capture.</p>
          <button className="evidence-button primary wide" disabled={!capture || !!busy} onClick={() => void controller.extract()}><ScanEye size={16} />{busy === 'extracting' ? 'Inspecting retained evidence…' : observation || state.error?.attemptId ? 'Run again on this capture' : 'Extract observations'}</button>
          {busy === 'extracting' && <p className="evidence-caption" role="status">Preparing source frames and waiting for Hydrogen. You can close this panel; the result stays with this camera.</p>}
          {observation && <div className="evidence-observations"><div className="evidence-result-summary"><strong>{observation.observations.length} evidence-linked observations</strong><span>gemini-4-hydrogen</span></div><ul>{observation.observations.map(item => <li key={item.id}><div className="evidence-observation-heading"><span>{item.category}</span><small>{item.value === null ? 'Unknown' : item.provenance}</small></div><p>{item.value ?? item.unknownReason ?? 'Not observable in this source.'}</p>{item.value !== null && item.unknownReason && <p className="evidence-caption">{item.unknownReason}</p>}<div className="evidence-references">{item.evidence.map((ref, index) => <button key={`${ref.timeSeconds}-${index}`} onClick={() => { setShowOriginal(true); setReview({ time: ref.timeSeconds, region: ref.region, revision: Date.now() }); }}><ScanEye size={12} />{formatEvidenceTime(ref.timeSeconds)}{ref.region ? ' · region' : ''}</button>)}</div></li>)}</ul>
            {observation.unknowns.length > 0 && <details className="evidence-unknowns"><summary>Uncertainty and limitations ({observation.unknowns.length})</summary><ul>{observation.unknowns.map((value, index) => <li key={index}>{value}</li>)}</ul></details>}
            <div className="evidence-result-links"><a href={apiUrl(`/api/observations/${encodeURIComponent(observation.id)}/evidence`)} target="_blank" rel="noreferrer">View frame audit <ArrowUpRight size={12} /></a>{state.attemptId && <a href={apiUrl(`/api/observations/attempts/${encodeURIComponent(state.attemptId)}`)} target="_blank" rel="noreferrer">View attempt <ArrowUpRight size={12} /></a>}</div>
          </div>}
        </section>
        <div className="evidence-next-stage"><strong>World exploration</strong><p>An accepted reference image and a connected world session are required before this camera can become playable.</p><button className="evidence-button secondary" disabled>Play generated world</button></div>
      </>}
    </div>
  </aside>;
}
