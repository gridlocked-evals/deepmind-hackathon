import { useEffect, useState } from 'react';
import { apiUrl, evidenceApi, errorText } from './api';
import type { EditOptions } from './api';
import type { EvidenceController, EvidenceState } from './controller';
import { selectedGate, selectedReference } from './controller';
import { WorldPreview } from './WorldPreview';
export function PipelinePanel({ state, controller, token }: { state: EvidenceState; controller: EvidenceController; token: string }) {
  const [options, setOptions] = useState<EditOptions | null>(null), [optionsError, setOptionsError] = useState('');
  const [preset, setPreset] = useState('protected_bike_lane'), [request, setRequest] = useState('Create a protected bike lane while preserving sidewalks, crosswalks, signals and building facades.');
  const [batchId, setBatchId] = useState(''), [style, setStyle] = useState('photoreal'), [worldActive, setWorldActive] = useState(false);
  useEffect(() => { let disposed = false; void evidenceApi.options().then(value => { if (!disposed) setOptions(value); }).catch(error => { if (!disposed) setOptionsError(error.message); }); return () => { disposed = true; }; }, []);
  const batches = state.extractions.flatMap(report => report.batchIds.map(id => ({ id, report })));
  const selectedBatch = batches.some(batch => batch.id === batchId) ? batchId : batches.at(-1)?.id ?? '';
  const reference = selectedReference(state), gate = selectedGate(state), edit = state.edit;
  const blocked = !!state.busy || worldActive || token.trim().length < 32;
  return <>
    <section className="evidence-section">
      <div className="evidence-section-heading"><h3>2. Propose a street edit</h3><span className="evidence-paid-label">Carbon · paid</span></div>
      <p className="evidence-description">Choose an accepted observation batch and describe a change. Original pixels and preserved scene anchors constrain the proposal.</p>
      {!batches.length ? <p className="evidence-empty">An extraction with a usable observation batch is required.</p> : <>
        <label className="evidence-field">Observation batch<select value={selectedBatch} onChange={event => setBatchId(event.target.value)} disabled={!!state.busy || worldActive}>{batches.map(batch => <option key={batch.id} value={batch.id}>{batch.id} · {batch.report.status}</option>)}</select></label>
        <label className="evidence-field">Edit preset<select value={preset} onChange={event => setPreset(event.target.value)} disabled={!options || !!state.busy || worldActive}>{Object.entries(options?.presets ?? {}).map(([id, spec]) => <option key={id} value={id}>{spec.title}</option>)}</select></label>
        <label className="evidence-field">Mayor’s request<textarea value={request} maxLength={2000} rows={3} disabled={!!state.busy || worldActive} onChange={event => setRequest(event.target.value)} /></label>
        <button className="evidence-button primary wide" disabled={blocked || !options || !request.trim() || !selectedBatch} onClick={() => void controller.compile(selectedBatch, request.trim(), preset, token)}>{state.busy === 'compiling' ? 'Compiling edit…' : 'Compile edit · paid call'}</button>
      </>}
      {optionsError && <p className="evidence-inline-error">{optionsError}</p>}
      {edit && <div className="evidence-stage-result"><strong>Proposal {edit.streetEdit.status.replaceAll('_', ' ')}</strong><p className="evidence-caption">{edit.streetEdit.id} · {edit.streetEdit.modelCall.modelId}</p>
        {edit.streetEdit.rejectionReason && <p>{edit.streetEdit.rejectionReason}</p>}
        <ul>{edit.streetEdit.operations.map(operation => <li key={operation.id}><strong>{operation.op} {operation.target.replaceAll('_', ' ')}</strong> · {operation.description}</li>)}</ul>
        <p className="evidence-caption">Inserted proposal · {edit.preservedAnchorIds.length} preserved anchors. This is not an approved traffic design.</p>
        {!!edit.validationErrors.length && <ul className="evidence-inline-error">{edit.validationErrors.map((error, i) => <li key={i}>{error}</li>)}</ul>}
        {!!edit.streetEdit.unknowns.length && <details className="evidence-unknowns"><summary>Proposal unknowns</summary><ul>{edit.streetEdit.unknowns.map((unknown, i) => <li key={i}>{unknown.field}: {unknown.reason}</li>)}</ul></details>}
      </div>}
    </section>
    {edit && <section className="evidence-section">
      <div className="evidence-section-heading"><h3>3. Generate a reference</h3><span className="evidence-paid-label">Nano Banana · paid</span></div>
      <p className="evidence-description">Generate one proposed image at a time, then compare it with the retained original. Every failed or rejected attempt remains in the history.</p>
      <label className="evidence-field">Image style<select value={style} disabled={blocked} onChange={event => setStyle(event.target.value)}>{Object.entries(options?.styles ?? { photoreal: 'supported', cel_shaded: 'supported' }).filter(([, status]) => status === 'supported').map(([id]) => <option value={id} key={id}>{id.replaceAll('_', ' ')}</option>)}</select></label>
      <button className="evidence-button primary wide" disabled={blocked || edit.streetEdit.status !== 'compiled' || !edit.sourceFrame || state.references.length >= (options?.maxReferenceAttempts ?? 3)} onClick={() => void controller.generate(style, gate?.state === 're_edit' ? gate.feedback : null, token)}>{state.busy === 'generating' ? 'Generating reference…' : gate?.state === 're_edit' ? 'Generate revision with gate feedback · paid' : 'Generate reference · paid call'}</button>
      {!edit.sourceFrame && <p className="evidence-caption">No original frame is bound to this edit. A reference cannot be generated.</p>}
      <button className="evidence-button subtle" disabled={!!state.busy} onClick={() => void controller.refreshReferences()}>Refresh retained attempts</button>
      {state.references.length > 0 && <label className="evidence-field">Attempt history<select value={state.selectedReferenceId ?? ''} onChange={event => controller.chooseReference(event.target.value)} disabled={!!state.busy || worldActive}>{state.references.map(attempt => <option key={attempt.id} value={attempt.id}>Attempt {attempt.attempt} · {attempt.status} · {attempt.style}</option>)}</select></label>}
      {reference && <><p className="evidence-caption">{reference.id} · {reference.status} · model {reference.modelCall?.modelId ?? 'unreported'}</p>
        {reference.error && <p className="evidence-inline-error" role="status">{errorText(reference.error)}</p>}
        {reference.status === 'generated' && <div className="evidence-comparison"><figure><img src={apiUrl(`/api/edits/${encodeURIComponent(edit.streetEdit.id)}/source-frame`)} alt="Retained original camera frame used for the street edit" /><figcaption>Original source frame</figcaption></figure><figure><img src={apiUrl(`/api/references/${encodeURIComponent(reference.id)}/image`)} alt="Generated street edit reference, not original camera evidence" /><figcaption>Generated proposal · attempt {reference.attempt}</figcaption></figure></div>}
      </>}
    </section>}
    {reference?.status === 'generated' && <section className="evidence-section">
      <div className="evidence-section-heading"><h3>4. Review image fidelity</h3><span className="evidence-paid-label">Decisions · paid</span></div>
      <p className="evidence-description">Check the selected image against the edit and original scene. Unknown or failed checks block world creation.</p>
      <button className="evidence-button primary wide" disabled={blocked || !!gate} onClick={() => void controller.gate(token)}>{state.busy === 'gating' ? 'Checking image…' : gate ? 'Gate recorded for this image' : 'Run image gate · paid call'}</button>
      {gate && <div className="evidence-stage-result"><strong>{gate.state.replaceAll('_', ' ')}</strong><ul className="evidence-checks">{gate.gateDecision.checks.map((check, index) => <li key={index}><span data-outcome={check.answer}>{check.answer}</span><div><strong>{check.question}</strong><p>{check.rationale}</p><small>Score {check.score === null ? 'unknown' : check.score.toFixed(3)}</small></div></li>)}</ul>
        {gate.feedback && <p className="evidence-notice">Revision feedback: {gate.feedback}</p>}
        <p className="evidence-caption">{gate.disclaimer ?? 'Visual fidelity judgments do not establish physical safety, collision rates or operational readiness.'}</p>
      </div>}
    </section>}
    <WorldPreview key={state.selectedReferenceId ?? 'no-reference'} reference={reference} gate={gate} token={token} onActive={setWorldActive} />
  </>;
}
