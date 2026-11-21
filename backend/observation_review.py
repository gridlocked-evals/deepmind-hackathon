"""Human annotation export and error reports. Never manufacture live labels."""
from __future__ import annotations

import base64
from html import escape
import json
from pathlib import Path

from .artifacts import ArtifactError, ArtifactStore, new_id, sha256, utcnow
from .gateway.contracts import canonical
from .observation_contracts import ManualReview
from .observations import intersects, iou


def verified_review(value: dict, report: dict) -> dict:
    annotation = value.get('annotation', {})
    content = {key: item for key, item in value.items() if key != 'sha256'}
    if (annotation.get('extractionId') != report['id']
            or annotation.get('extractionSha256') != sha256(canonical(report))
            or value.get('sha256') != sha256(canonical(content))):
        raise ArtifactError('Review does not match its original extraction or content hash.', status=409)
    return value


def annotation_template(report: dict) -> dict:
    return {'extractionId': report['id'], 'captureId': report['captureId'],
        'prefixSha256': report['prefixSha256'], 'reviewer': '', 'provenance': 'human_annotation',
        'extractionSha256': sha256(canonical(report)), 'reviewedOriginalPixels': False,
        'reviewMode': 'model_assisted', 'annotations': []}


def failure_report(report: dict) -> dict:
    """Report engineering coverage without claiming human accuracy."""
    windows = report['windows']
    return {'schemaVersion': 'street-evidence-failures.v1', 'extractionId': report['id'],
        'extractionSha256': sha256(canonical(report)), 'captureId': report['captureId'],
        'prefixSha256': report['prefixSha256'], 'status': report['status'],
        'plannedWindows': report.get('plannedWindows', len(windows)), 'metrics': report['metrics'],
        'windows': [{'attemptId': window['attemptId'], 'status': window['status'],
            'time': {'startS': window['startS'], 'endS': window['endS']},
            'frameIds': window['frameIds'], 'gatewayRequestId': window.get('gatewayRequestId'),
            'error': window.get('error'), 'unknowns': window.get('unknowns', [])} for window in windows],
        'unresolvedFacts': [{'factId': fact['id'], 'kind': fact['kind'], 'disposition': fact['disposition'],
            'time': fact['time'], 'region': fact['region']} for fact in report['facts'] if fact['disposition'] != 'accepted'],
        'failures': report['failures'], 'humanAccuracy': None,
        'notice': 'Failed, abstained and unprocessed windows remain in the report. No human accuracy is inferred from model acceptance.'}


def score_review(store: ArtifactStore, report: dict, review: ManualReview) -> dict:
    if (review.extractionId != report['id'] or review.captureId != report['captureId']
            or review.prefixSha256 != report['prefixSha256'] or review.extractionSha256 != sha256(canonical(report))):
        raise ArtifactError('Human annotations must reference this exact capture and extraction.', status=422)
    allowed = {frame_id for window in report['windows'] for frame_id in window['frameIds']}
    seen_annotations = set()
    comparisons = []
    for expected in review.annotations:
        encoded = canonical({'kind': expected.kind, 'semanticClass': expected.semanticClass,
            'time': expected.time.model_dump(), 'region': expected.region.model_dump(),
            'frameIds': sorted(expected.frameIds)})
        if encoded in seen_annotations:
            raise ArtifactError('Duplicate annotations cannot count as independent reviews.', status=422)
        seen_annotations.add(encoded)
        if not set(expected.frameIds) <= allowed:
            raise ArtifactError('Annotations may reference only original frames in this extraction.', status=422)
        frames = [store.get('frames', frame_id) for frame_id in expected.frameIds]
        for frame in frames:
            if (frame['captureId'] != report['captureId'] or frame['captureMediaSha256'] != report['prefixSha256']):
                raise ArtifactError('Annotation frame provenance does not match this capture.', status=409)
            store.get_blob(frame['frameSha256'])
        times = [frame['mediaTimeS'] for frame in frames]
        if (expected.time.startS < min(times) or expected.time.endS > max(times)
                or (expected.kind == 'count' and (len(frames) != 1 or expected.time.startS != times[0]))
                or (expected.kind == 'event' and (report['sourceKind'] != 'hls' or len(set(times)) < 2
                    or len({frame['frameSha256'] for frame in frames}) < 2))):
            raise ArtifactError('Annotation time or event is not supported by the cited original frames.', status=422)
        if not (report['time']['startS'] <= expected.time.startS <= expected.time.endS <= report['time']['endS']):
            raise ArtifactError('Annotation lies outside the reviewed prefix interval.', status=422)
        region = expected.region.model_dump()
        candidates = [r for r in report['facts'] if r['kind'] == expected.kind
                      and r['semanticClass'] == expected.semanticClass
                      and intersects(r['time'], expected.time.model_dump())
                      and iou(r['region'], region) > 0]
        accepted = [r for r in candidates if r['disposition'] == 'accepted']
        # An absence annotation contradicts every accepted matching claim. A
        # closer abstention must not conceal those false positives.
        matches = accepted if expected.present == 'no' and accepted else candidates
        best = max(matches, key=lambda r: iou(r['region'], region), default=None)
        fact_ids = [r['id'] for r in accepted] if expected.present == 'no' and accepted else ([best['id']] if best else [])
        presence = 'human_unknown' if expected.present == 'unknown' else 'missed'
        processed = [window for window in report['windows'] if window['status'] in {'ok', 'abstained'}
            and window['startS'] <= expected.time.startS <= expected.time.endS <= window['endS']
            and set(expected.frameIds) <= set(window['frameIds'])]
        if expected.present != 'unknown' and not processed:
            presence = 'model_failed_or_unprocessed'
        elif expected.present == 'no':
            if expected.kind == 'count' and accepted:
                comparable = [r for r in accepted if iou(r['region'], region) >= .95]
                contradictions = [r for r in comparable if r['count']['lower'] is not None and r['count']['lower'] > 0]
                if contradictions:
                    best = max(contradictions, key=lambda r: iou(r['region'], region))
                    presence = 'false_positive'
                elif len(comparable) != len(accepted):
                    presence = 'count_region_mismatch_unscored'
                elif any(r['count']['lower'] != 0 or r['count']['upper'] != 0 for r in comparable):
                    presence = 'model_abstained'
                else:
                    presence = 'correct_absence'
            else:
                presence = 'false_positive' if accepted else ('model_abstained'
                    if candidates or not any(window['status'] == 'ok' for window in processed) else 'correct_absence')
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
        elif expected.present == 'yes' and expected.kind == 'signal' and expected.state in {None, 'unknown'}:
            outcome = 'human_unknown'
        elif presence == 'matched':
            if expected.kind == 'count':
                got = best['count']
                if iou(best['region'], region) < .95:
                    outcome = 'count_region_mismatch_unscored'
                elif got['lower'] is None:
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
    result = {'id': new_id('review'), 'createdAt': utcnow().isoformat(),
              'schemaVersion': 'street-evidence-review.v1', 'annotation': review.model_dump(mode='json'), 'comparisons': comparisons,
              'extractionMetrics': report['metrics'], 'unmatchedAcceptedFacts': [r['id'] for r in report['facts']
                  if r['disposition'] == 'accepted' and not any(r['id'] in c['factIds'] for c in comparisons)],
              'counts': {name: sum(c['outcome'] == name for c in comparisons) for name in sorted({c['outcome'] for c in comparisons})},
              'notice': 'Unannotated facts are unreviewed, not automatically false positives. Interval overlap is not exact count accuracy. Presence matches do not score counts without human bounds.'}
    unscored = {'human_unknown', 'count_region_mismatch_unscored', 'model_failed_or_unprocessed'}
    result['coverage'] = {'annotations': len(comparisons),
        'scoredComparisons': sum(item['outcome'] not in unscored for item in comparisons),
        'unscoredComparisons': sum(item['outcome'] in unscored for item in comparisons),
        'unreviewedAcceptedFacts': len(result['unmatchedAcceptedFacts'])}
    result['sha256'] = sha256(canonical(result))
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
        f'<p>Extraction hash: {escape(sha256(canonical(report)))}</p>'
        '<h2>Original evidence</h2><p>For an independent review, record your own observations before opening the model table below. '
        'Each annotation needs original frame IDs, a media-relative interval and an original-image normalized box. '
        'Unknown values should remain unknown.</p><div class="frames">'+''.join(figures)+'</div>'
        '<h2>Manual annotation template</h2><p>This template contains no human labels. Set reviewedOriginalPixels to true only '
        'after personally checking the cited frames. Use model_assisted if you consult the model table. '
        'Save and complete the template, then submit it through the annotation endpoint or offline review command.</p>'
        f'<pre>{escape(json.dumps(annotation_template(report),indent=2))}</pre>'
        f'<p>Metrics: {escape(json.dumps(report["metrics"]))}</p><details><summary>Model facts and evidence links</summary>'
        '<table><thead><tr><th>Kind</th><th>Fact</th>'
        '<th>Disposition</th><th>Bounds and original-image ROI</th><th>Raw evidence</th></tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table></details><h2>Failures and abstentions</h2><pre>'+escape(json.dumps(
            {'failures':report['failures'], 'unknowns':[w.get('unknowns',[]) for w in report['windows']]},indent=2))+'</pre>')


def main(argv=None) -> int:
    """Offline review/export only. No model calls or generated human labels."""
    import argparse
    from pydantic import ValidationError
    parser = argparse.ArgumentParser(description='Export raw-pixel review packets or import explicit human annotations; never calls a model.')
    parser.add_argument('--artifact-dir', type=Path, required=True)
    parser.add_argument('--extraction', required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--export-dir', type=Path)
    actions.add_argument('--annotations', type=Path)
    args = parser.parse_args(argv)
    try:
        store = ArtifactStore(args.artifact_dir)
        report = store.get('extractions', args.extraction)
        if args.annotations:
            review = ManualReview.model_validate_json(args.annotations.read_text('utf-8'))
            result = score_review(store, report, review)
            print(json.dumps({'reviewId': result['id'], 'sha256': result['sha256'], 'counts': result['counts']}))
        else:
            args.export_dir.mkdir(parents=True, exist_ok=True)
            packet = {'review.html': review_html(store, report),
                      'annotations.template.json': json.dumps(annotation_template(report), indent=2),
                      'failures.json': json.dumps(failure_report(report), indent=2),
                      'original-frames.json': json.dumps([store.get('frames', frame_id) for frame_id in dict.fromkeys(
                          frame_id for window in report['windows'] for frame_id in window['frameIds'])], indent=2)}
            # Export never overwrites a person's edited annotations or review.
            if any((args.export_dir / name).exists() for name in packet):
                raise ArtifactError('Export files already exist; choose a new output directory.', status=409)
            for name, content in packet.items():
                with (args.export_dir / name).open('x', encoding='utf-8') as handle:
                    handle.write(content)
            print(json.dumps({'extractionId': report['id'], 'extractionSha256': sha256(canonical(report)),
                              'status': 'optional_not_reviewed', 'exportDirectory': str(args.export_dir)}))
        return 0
    except (ArtifactError, OSError, ValueError, ValidationError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, ArtifactError) else 'Invalid review input or inaccessible review files.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
