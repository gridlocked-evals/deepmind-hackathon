import { useEffect, useMemo, useRef, useState } from 'react';
import { HumanControls } from './worldControls';
import { apiUrl, canCreateWorld, errorText, worldApi } from './api';
import type { GateRecord, ReferenceAttempt, WorldStatus, WorldResults } from './api';
export const canSendMovement = (status: Pick<WorldStatus, 'state'> | null, hasFrame: boolean) => status?.state === 'running' && hasFrame;
const terminal = (state: string) => ['completed', 'stopped', 'failed', 'termination_unconfirmed'].includes(state);
const keyMap: Record<string, string> = { w: 'key_w', a: 'key_a', s: 'key_s', d: 'key_d', ArrowUp: 'arrow_up', ArrowDown: 'arrow_down', ArrowLeft: 'arrow_left', ArrowRight: 'arrow_right' };
export function WorldPreview({ reference, gate, token, onActive }: { reference: ReferenceAttempt | null; gate: GateRecord | null; token: string; onActive: (active: boolean) => void }) {
  const [enabled, setEnabled] = useState(false), [status, setStatus] = useState<WorldStatus | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [frame, setFrame] = useState(''), [results, setResults] = useState<WorldResults | null>(null);
  const socket = useRef<WebSocket | null>(null), mounted = useRef(true), session = useRef<WorldStatus | null>(null);
  const sessionToken = useRef(''), heartbeat = useRef<ReturnType<typeof setInterval> | null>(null), onActiveRef = useRef(onActive); onActiveRef.current = onActive;
  const fetchResults = async (id: string) => { try { const value = await worldApi.results(id, sessionToken.current); if (mounted.current) { setResults(value); session.current = value.session; setStatus(value.session); if (terminal(value.session.state)) onActiveRef.current(false); } } catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : 'Retained results are not available yet.'); } };
  const controls = useMemo(() => new HumanControls(command => { if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(command)); }), []);
  const stop = async () => {
    controls.stop();
    if (!session.current) return;
    setBusy(true);
    try { const value = await worldApi.stop(session.current.session_id, sessionToken.current); if (mounted.current) { session.current = value; setStatus(value); } await fetchResults(value.session_id); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : 'Stop was not confirmed.'); }
    finally { if (mounted.current) setBusy(false); }
  };
  useEffect(() => {
    mounted.current = true;
    const release = () => controls.release();
    const hidden = () => { if (document.hidden) release(); };
    window.addEventListener('blur', release); document.addEventListener('visibilitychange', hidden);
    return () => {
      mounted.current = false; controls.stop();
      if (heartbeat.current) clearInterval(heartbeat.current);
      socket.current?.close();
      if (session.current && !terminal(session.current.state)) void worldApi.stop(session.current.session_id, sessionToken.current).catch(() => {});
      onActiveRef.current(false); window.removeEventListener('blur', release); document.removeEventListener('visibilitychange', hidden);
    };
  }, [controls]);
  const start = async () => {
    if (!enabled || !reference || !gate || !canCreateWorld(gate, reference) || busy || (status && !terminal(status.state))) return;
    setBusy(true); setError(''); setFrame(''); setResults(null); onActive(true); sessionToken.current = token;
    try {
      const created = await worldApi.create(reference, gate, token);
      if (!mounted.current) { await worldApi.stop(created.session_id, token); return; }
      session.current = created; setStatus(created);
      const url = new URL(apiUrl(created.websocket_path), window.location.href); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const connection = new WebSocket(url); socket.current = connection;
      connection.onopen = () => {
        connection.send(JSON.stringify({ role: 'human', token: created.connect_tokens.human }));
        heartbeat.current = setInterval(() => { if (connection.readyState === WebSocket.OPEN) connection.send(JSON.stringify({ kind: 'heartbeat' })); }, 1000);
      };
      connection.onmessage = event => {
        if (!mounted.current) return;
        try {
          const item = JSON.parse(event.data);
          if (item.kind === 'status' && item.session_id === created.session_id) {
            session.current = item; setStatus(item);
            if (terminal(item.state)) { controls.clear(); onActiveRef.current(false); void fetchResults(created.session_id); }
          } else if (item.kind === 'frame' && ['image/jpeg', 'image/png'].includes(item.mime_type) && typeof item.data === 'string') setFrame(`data:${item.mime_type};base64,${item.data}`);
        } catch { setError('An invalid world stream message was ignored.'); }
      };
      connection.onerror = () => { if (mounted.current) setError('The world stream connection failed. Check the retained session and stop confirmation.'); };
      connection.onclose = () => {
        if (heartbeat.current) clearInterval(heartbeat.current); controls.clear();
        if (mounted.current) { onActiveRef.current(false); if (session.current && !terminal(session.current.state)) setError('The stream disconnected. The backend requests a stop; refresh results to confirm termination.'); void fetchResults(created.session_id); }
      };
    } catch (cause) { if (mounted.current) { setError(cause instanceof Error ? cause.message : 'World creation failed.'); onActiveRef.current(false); } }
    finally { if (mounted.current) setBusy(false); }
  };
  const active = !!status && !terminal(status.state);
  return <section className="evidence-section">
    <div className="evidence-section-heading"><h3>5. Optional world</h3><span className="evidence-paid-label">Genie · paid</span></div>
    <label className="evidence-checkbox"><input type="checkbox" checked={enabled} disabled={active || busy} onChange={event => setEnabled(event.target.checked)} />Enable a generated world for this review</label>
    <p className="evidence-description">Evidence, edits and image review work with Genie off. A world requires this image to pass every gate and Genie to be enabled in the backend.</p>
    {enabled && <><p className="evidence-caption">One stochastic session · up to 15 seconds · local allowance $2.50. No deterministic replay or automated driver. The live temporal judge is not connected. Closing this panel or changing the source requests a stop.</p>
      <button className="evidence-button primary wide" onClick={() => void start()} disabled={busy || active || !!status || !canCreateWorld(gate, reference) || token.trim().length < 32}>{busy && !status ? 'Creating world…' : 'Create world · paid call'}</button>
      {!canCreateWorld(gate, reference) && <p className="evidence-caption">An accepted gate for the selected image is required. Failed and unknown checks block creation.</p>}
    </>}
    {status && <div className="world-review"><p className="evidence-caption">Session {status.session_id} · {status.state} · {status.frames_received} frames received</p>
      <div className="world-viewport" tabIndex={0} role="group" aria-label="Generated world controls: W A S D and arrow keys, release on key up" onBlur={() => controls.release()}
        onKeyDown={event => { const key = keyMap[event.key] ?? keyMap[event.key.toLowerCase()]; if (key && canSendMovement(status, !!frame)) { event.preventDefault(); controls.keyDown(key, event.repeat); } }}
        onKeyUp={event => { if (keyMap[event.key] ?? keyMap[event.key.toLowerCase()]) { event.preventDefault(); controls.keyUp(keyMap[event.key] ?? keyMap[event.key.toLowerCase()]); } }}>
        {frame ? <img src={frame} alt="Generated Genie world frame, not original camera evidence" /> : <span>Waiting for generated frames</span>}
      </div><p className="evidence-caption">Generated world · focus the image to use W A S D or arrows. Each press is a pulse of up to 200 ms; release and press again to move further; losing focus releases movement. Control owner: {status.key_owner}.</p>
      <div className="world-controls">{[['↑', 'key_w', 'Move forward'], ['←', 'key_a', 'Move left'], ['↓', 'key_s', 'Move backward'], ['→', 'key_d', 'Move right']].map(([label, key, name]) => <button key={key} className="evidence-button secondary" aria-label={name} disabled={!canSendMovement(status, !!frame)} onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); controls.keyDown(key); }} onPointerUp={() => controls.keyUp(key)} onPointerCancel={() => controls.release()} onKeyDown={event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); controls.keyDown(key, event.repeat); } }} onKeyUp={event => { if (event.key === ' ' || event.key === 'Enter') controls.keyUp(key); }} onBlur={() => controls.release()}>{label}</button>)}</div>
      <div className="evidence-actions"><button className="evidence-button secondary" disabled={status.state !== 'running'} onClick={() => controls.takeover()}>Take human control</button><button className="evidence-button danger" disabled={!active || busy} onClick={() => void stop()}>Stop world</button><button className="evidence-button subtle" onClick={() => void fetchResults(status.session_id)}>Refresh results</button></div>
      {status.error && <p className="evidence-inline-error">{errorText(status.error)}</p>}
      <p className="evidence-caption">Stop reason: {status.stop_reason ?? 'not reported'} · provider termination: {status.termination ?? 'not confirmed'}</p>
    </div>}
    {results && <details className="evidence-unknowns" open><summary>Retained session results</summary><p>{results.frames.length} recorded frames · {results.controls.length} control events. Temporal judge: {results.temporalJudge.status}; {results.temporalJudge.uncoveredFrames} frames unjudged.</p><p className="evidence-caption">{results.temporalJudge.reason}</p><details><summary>Session, usage and provenance</summary><pre className="evidence-json">{JSON.stringify(results.session, null, 2)}</pre></details></details>}
    {error && <p className="evidence-inline-error" role="alert">{error}</p>}
  </section>;
}
