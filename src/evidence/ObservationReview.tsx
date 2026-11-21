import { useEffect, useState } from 'react';
import { apiUrl } from './api';
import type { ExtractionReport, FrameEvidence } from './api';
export function ObservationReview({ report, token, reference }: { report: ExtractionReport; token: string; reference: FrameEvidence | null }) {
  const [image, setImage] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    if (!reference || !token) { setImage(''); return; }
    const controller = new AbortController(); let url = ''; setImage(''); setError('');
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/observations/extractions/${encodeURIComponent(report.id)}/frames/${encodeURIComponent(reference.frameId)}`), { signal: controller.signal, headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) throw new Error('The original observation frame could not be loaded. Check the operator token.');
        if (response.headers.get('X-Content-Sha256') !== reference.frameSha256) throw new Error('Original frame hash did not match the evidence reference.');
        const bytes = await response.arrayBuffer();
        const digest = await crypto.subtle.digest('SHA-256', bytes); const actual = `sha256:${[...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')}`;
        if (actual !== reference.frameSha256) throw new Error('Original frame bytes failed their integrity check.');
        if (!controller.signal.aborted) { url = URL.createObjectURL(new Blob([bytes], { type: response.headers.get('Content-Type') ?? 'image/jpeg' })); setImage(url); }
      } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Frame review failed.'); }
    })();
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [report.id, reference, token]);
  return <div className="evidence-human-review">
    {reference && <div className="evidence-original"><div className="evidence-media-frame">{image ? <><img src={image} alt={`Verified original frame ${reference.frameIndex} at ${reference.mediaTimeS.toFixed(3)} seconds`} /><span className="evidence-roi" style={{ left: `${reference.roi.x * 100}%`, top: `${reference.roi.y * 100}%`, width: `${reference.roi.w * 100}%`, height: `${reference.roi.h * 100}%` }} /></> : <p className="evidence-empty">Loading the original evidence frame…</p>}</div><p className="evidence-caption">Original frame {reference.frameIndex} · actual media time {reference.mediaTimeS.toFixed(6)} s. Highlighted region is the model’s cited ROI. Exposure time remains separate.</p></div>}
    {error && <p className="evidence-inline-error" role="alert">{error}</p>}
  </div>;
}
