import { useEffect, useState } from 'react';
import { apiUrl, request, requireToken } from './api';
import type { ExtractionReport, FrameEvidence } from './api';
export function ObservationReview({ report, token, reference }: { report: ExtractionReport; token: string; reference: FrameEvidence | null }) {
  const [image, setImage] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [annotation, setAnnotation] = useState<Record<string, unknown> | null>(null), [attested, setAttested] = useState(false), [reviewResult, setReviewResult] = useState<unknown>(null);
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
  const download = async (kind: 'annotation-template' | 'review' | 'failure-report') => {
    setBusy(true); setError('');
    try {
      requireToken(token);
      const response = await fetch(apiUrl(`/api/observations/extractions/${encodeURIComponent(report.id)}/${kind}`), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error('The review packet could not be loaded. Check the operator token and retry.');
      const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${report.id}-${kind}.${kind === 'review' ? 'html' : 'json'}`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Download failed.'); } finally { setBusy(false); }
  };
  const submit = async () => {
    if (!annotation || !attested) return; setBusy(true); setError('');
    try {
      requireToken(token);
      if (annotation.extractionId !== report.id || annotation.captureId !== report.captureId || annotation.prefixSha256 !== report.prefixSha256 || annotation.reviewedOriginalPixels !== true) throw new Error('Annotations must reference this extraction and attest to original-pixel review. Complete its template before submitting.');
      const result = await request(`/api/observations/extractions/${encodeURIComponent(report.id)}/annotations`, { method: 'POST', body: JSON.stringify(annotation) }, token); setReviewResult(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Review submission failed.'); } finally { setBusy(false); }
  };
  return <div className="evidence-human-review">
    {reference && <div className="evidence-original"><div className="evidence-media-frame">{image ? <><img src={image} alt={`Verified original frame ${reference.frameIndex} at ${reference.mediaTimeS.toFixed(3)} seconds`} /><span className="evidence-roi" style={{ left: `${reference.roi.x * 100}%`, top: `${reference.roi.y * 100}%`, width: `${reference.roi.w * 100}%`, height: `${reference.roi.h * 100}%` }} /></> : <p className="evidence-empty">Loading the original evidence frame…</p>}</div><p className="evidence-caption">Original frame {reference.frameIndex} · actual media time {reference.mediaTimeS.toFixed(6)} s. Highlighted region is the model’s cited ROI. Exposure time remains separate.</p></div>}
    <details className="evidence-unknowns"><summary>Review original pixels and annotate</summary><p>Human accuracy is unknown until you review original frames and submit your own annotations. A downloaded HTML packet contains original pixels with the model table collapsed.</p>
      <div className="evidence-actions"><button className="evidence-button subtle" disabled={busy || token.trim().length < 32} onClick={() => void download('review')}>Download original-pixel review</button><button className="evidence-button subtle" disabled={busy || token.trim().length < 32} onClick={() => void download('annotation-template')}>Download empty annotation template</button><button className="evidence-button subtle" disabled={busy || token.trim().length < 32} onClick={() => void download('failure-report')}>Download failure report</button></div>
      <label className="evidence-field">Completed annotation JSON<input type="file" accept="application/json,.json" disabled={busy} onChange={event => {
        const file = event.target.files?.[0]; setAnnotation(null); setAttested(false); if (!file) return;
        if (file.size > 1024 * 1024) { setError('Annotation file must be smaller than 1 MiB.'); return; }
        void file.text().then(text => { const value = JSON.parse(text); if (!value || typeof value !== 'object') throw new Error(); setAnnotation(value); }).catch(() => setError('The annotation file is not valid JSON.'));
      }} /></label>
      <label className="evidence-checkbox"><input type="checkbox" checked={attested} onChange={event => setAttested(event.target.checked)} />I reviewed the cited original pixels and completed these labels myself.</label>
      <p className="evidence-caption">Use model_assisted review mode if you consulted model facts. Unknown labels remain unknown; they are not counted as correct.</p>
      <button className="evidence-button secondary" disabled={busy || !annotation || !attested || token.trim().length < 32} onClick={() => void submit()}>Submit my annotations</button>
      {reviewResult !== null && <details open><summary>Retained annotation result</summary><pre className="evidence-json">{JSON.stringify(reviewResult, null, 2)}</pre></details>}
    </details>
    {error && <p className="evidence-inline-error" role="alert">{error}</p>}
  </div>;
}
