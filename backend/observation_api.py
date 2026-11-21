"""Operator-authenticated extraction and review over saved capture prefixes."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from .artifacts import ArtifactError
from .capture import CaptureError
from .gateway import GatewayError
from .observation_contracts import ExtractRequest, ManualReview, ReinspectRequest
from .observation_review import annotation_template, failure_report, review_html, score_review, verified_review
from .observations import extract
from .pipeline_api import _fail
from .worlds.api import authorize

router = APIRouter(prefix='/api/observations', dependencies=[Depends(authorize)])


@router.post('/extract')
async def create(body: ExtractRequest, request: Request):
    state = request.app.state
    try:
        return await extract(state.models, state.artifacts, state.captures, body)
    except (ArtifactError, CaptureError, GatewayError) as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}')
async def get_extraction(job_id: str, request: Request):
    try:
        return request.app.state.artifacts.get('extractions', job_id)
    except ArtifactError as exc:
        return _fail(exc)


@router.get('/batches/{batch_id}/evidence')
async def batch_evidence(batch_id: str, request: Request):
    try:
        return request.app.state.artifacts.get('observation_evidence', batch_id)
    except ArtifactError as exc:
        return _fail(exc)


@router.post('/extractions/{job_id}/reinspect')
async def reinspect(job_id: str, body: ReinspectRequest, request: Request):
    state = request.app.state
    try:
        parent = state.artifacts.get('extractions', job_id)
        if parent['parentId'] is not None:
            raise ArtifactError('Reinspection cannot recursively create further reinspection jobs.', status=422)
        selection = ExtractRequest(captureId=parent['captureId'], cameraId=parent['sourceId'],
                                   startS=body.time.startS, endS=body.time.endS)
        return await extract(state.models, state.artifacts, state.captures, selection, parent=parent, reinspection=body)
    except (ArtifactError, CaptureError, GatewayError) as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}/review')
async def review(job_id: str, request: Request):
    store = request.app.state.artifacts
    try:
        return HTMLResponse(review_html(store, store.get('extractions', job_id)),
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except ArtifactError as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}/annotation-template')
async def template(job_id: str, request: Request):
    try:
        return annotation_template(request.app.state.artifacts.get('extractions', job_id))
    except ArtifactError as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}/failure-report')
async def failures(job_id: str, request: Request):
    try:
        return failure_report(request.app.state.artifacts.get('extractions', job_id))
    except ArtifactError as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}/reviews')
async def reviews(job_id: str, request: Request):
    store = request.app.state.artifacts
    try:
        report = store.get('extractions', job_id)
        rows = [verified_review(value, report) for value in store.list('reviews')
                if value.get('annotation', {}).get('extractionId') == job_id]
        return {'extractionId': job_id, 'reviews': rows,
                'status': 'reviewed_annotations_available' if rows else 'optional_not_reviewed',
                'notice': 'Repeated reviews are separate records; their annotations are not pooled as independent samples.'}
    except ArtifactError as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}/reviews/{review_id}')
async def review_record(job_id: str, review_id: str, request: Request):
    store = request.app.state.artifacts
    try:
        report = store.get('extractions', job_id)
        return verified_review(store.get('reviews', review_id), report)
    except ArtifactError as exc:
        return _fail(exc)


@router.post('/extractions/{job_id}/annotations')
async def annotate(job_id: str, body: ManualReview, request: Request):
    store = request.app.state.artifacts
    try:
        return score_review(store, store.get('extractions', job_id), body)
    except ArtifactError as exc:
        return _fail(exc)


@router.get('/extractions/{job_id}/frames/{frame_id}')
async def frame(job_id: str, frame_id: str, request: Request):
    store = request.app.state.artifacts
    try:
        report = store.get('extractions', job_id)
        if frame_id not in {f for w in report['windows'] for f in w['frameIds']}:
            raise ArtifactError('Frame was not part of this extraction.', status=403)
        record = store.get('frames', frame_id)
        return Response(store.get_blob(record['frameSha256']), media_type=record['mimeType'],
                        headers={'X-Content-Sha256':record['frameSha256'], 'Cache-Control':'private, no-store'})
    except ArtifactError as exc:
        return _fail(exc)
