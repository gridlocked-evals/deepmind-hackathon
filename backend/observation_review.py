"""Human annotation export and error reports. Never manufacture live labels."""
from __future__ import annotations

import base64
from html import escape
import json

from .artifacts import ArtifactError, ArtifactStore, new_id
from .observation_contracts import ManualReview
from .observations import intersects, iou


def annotation_template(report: dict) -> dict:
    return {'extractionId': report['id'], 'captureId': report['captureId'],
        'prefixSha256': report['prefixSha256'], 'reviewer': '', 'provenance': 'human_annotation',
        'annotations': []}


def score_review(store: ArtifactStore, report: dict, review: ManualReview) -> dict:
    if (review.extractionId != report['id'] or review.captureId != report['captureId']
            or review.prefixSha256 != report['prefixSha256']):
        raise ArtifactError('Human annotations must reference this exact capture and extraction.', status=422)
    comparisons = []
    for expected in review.annotations:
        if not (report['time']['startS'] <= expected.time.startS <= expected.time.endS <= report['time']['endS']):
            raise ArtifactError('Annotation lies outside the reviewed prefix interval.', status=422)
        region = expected.region.model_dump()
        candidates = [r for r in report['facts'] if r['kind']==expected.kind
                      and r['semanticClass']==expected.semanticClass and intersects(r['time'], expected.time.model_dump())
                      and iou(r['region'], region) > 0]
        accepted = [r for r in candidates if r['disposition'] == 'accepted']
        # An absence annotation contradicts every accepted matching claim. A
        # closer abstention must not conceal those false positives.
        matches = accepted if expected.present == 'no' and accepted else candidates
        best = max(matches, key=lambda r: iou(r['region'], region), default=None)
        fact_ids = [r['id'] for r in accepted] if expected.present == 'no' and accepted else ([best['id']] if best else [])
        presence = 'human_unknown' if expected.present == 'unknown' else 'missed'
        if expected.present == 'no':
            presence = 'false_positive' if accepted else ('model_abstained' if candidates else 'correct_absence')
        elif expected.present == 'yes' and best:
            if best['disposition'] != 'accepted':
                presence = 'model_abstained'
            elif iou(best['region'], region) < .5:
                presence = 'region_error'
            else:
                presence = 'matched'
        outcome = presence
        if expected.present == 'yes' and expected.kind == 'count' and expected.count.lower is None:
            # Presence alone supplies no ground truth for the quantity. Keep its
            # separate result without counting an unspecified count as a match.
            outcome = 'human_unknown'
        elif presence == 'matched':
            if expected.kind == 'count':
                got = best['count']
                if got['lower'] is None:
                    outcome = 'model_abstained'
                elif ((expected.count.upper is not None and got['lower'] > expected.count.upper)
                      or (got['upper'] is not None and got['upper'] < expected.count.lower)):
                    outcome = 'count_error'
                else:
                    outcome = 'count_interval_overlap'
            elif expected.state is not None and expected.state != best['state']:
                outcome = 'state_error'
        comparisons.append({'expected': expected.model_dump(), 'factId': best['id'] if best else None,
                            'factIds': fact_ids, 'outcome': outcome, 'presenceOutcome': presence,
                            'regionIoU': iou(best['region'], region) if best else None})
    result = {'id': new_id('review'), 'annotation': review.model_dump(mode='json'), 'comparisons': comparisons,
              'extractionMetrics': report['metrics'], 'unmatchedAcceptedFacts': [r['id'] for r in report['facts']
                  if r['disposition']=='accepted' and not any(r['id'] in c['factIds'] for c in comparisons)],
              'counts': {name: sum(c['outcome']==name for c in comparisons) for name in sorted({c['outcome'] for c in comparisons})},
              'notice': 'Unannotated facts are unreviewed, not automatically false positives. Interval overlap is not exact count accuracy. Presence matches do not score counts without human bounds.'}
    return store.put('reviews', result['id'], result)


def review_html(store: ArtifactStore, report: dict) -> str:
    frame_ids = list(dict.fromkeys(f for w in report['windows'] for f in w['frameIds']))
    figures = []
    for identifier in frame_ids:
        frame = store.get('frames', identifier)
        data = base64.b64encode(store.get_blob(frame['frameSha256'])).decode()
        figures.append(f'<figure id="{escape(identifier)}"><img src="data:{frame["mimeType"]};base64,{data}" '
                       f'alt="Original frame {frame["frameIndex"]}"><figcaption>Original frame {frame["frameIndex"]} · '
                       f'{frame["mediaTimeS"]:.6f} s · {escape(frame["frameSha256"])}</figcaption></figure>')
    rows = []
    for fact in report['facts']:
        links = ', '.join(f'<a href="#{escape(e["frameId"])}">{e["frameIndex"]} @ {e["mediaTimeS"]} s</a>' for e in fact['evidence'])
        rows.append('<tr>'+''.join('<td>'+s+'</td>' for s in (
            escape(fact['kind']), escape(fact['label']), escape(fact['disposition']),
            escape(json.dumps({'state': fact['state'], 'count': fact['count'], 'time': fact['time'], 'roi': fact['region']})), links))+'</tr>')
    return ('<!doctype html><meta charset="utf-8"><title>Street evidence review</title>'
        '<style>body{font:16px system-ui;margin:2rem;max-width:1400px;color:#17232c} '
        '.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:1rem}'
        'figure{margin:0}img{width:100%}figcaption{font-size:12px;overflow-wrap:anywhere}'
        'table{border-collapse:collapse;width:100%}td,th{padding:.6rem;border:1px solid #ddd;text-align:left}'
        'td{overflow-wrap:anywhere}pre{white-space:pre-wrap}</style>'
        f'<h1>Street evidence review</h1><p>Capture {escape(report["captureId"])} · Source {escape(report["sourceId"])} · '
        f'{escape(report["status"])}</p><p>Media-relative timestamps; exposure clock is {escape(report["clocks"]["sourceClock"])}. '
        'Review original pixels before annotating. No human accuracy result exists until annotations are submitted.</p>'
        f'<p>Metrics: {escape(json.dumps(report["metrics"]))}</p><table><thead><tr><th>Kind</th><th>Fact</th>'
        '<th>Disposition</th><th>Bounds and original-image ROI</th><th>Raw evidence</th></tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table><h2>Original evidence</h2><div class="frames">'+''.join(figures)
        + '</div><h2>Failures and abstentions</h2><pre>'+escape(json.dumps(
            {'failures':report['failures'], 'unknowns':[w.get('unknowns',[]) for w in report['windows']]},indent=2))+'</pre>')
