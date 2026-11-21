"""Scripted provider tests; fixture annotations here are not live human labels."""
import asyncio
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.artifacts import ArtifactError, ArtifactStore, sha256, utcnow
from backend.capture import CaptureStore
from backend.contracts import ObservationBatch
from backend.gateway import GatewayConfig, ModelGateway
from backend.observation_api import router
from backend.observation_contracts import ExtractRequest, Fact, ManualReview, ReinspectRequest, WireOutput
from backend.observation_media import index_prefix, verified_prefix
from backend.observation_review import annotation_template, failure_report, main as review_main, review_html, score_review, verified_review
from backend.observations import accept_facts, extract, windows
from backend.test_pipeline_helpers import png, USAGE

BOX = {'kind':'box', 'x':.1, 'y':.2, 'w':.3, 'h':.4}


def fact(meta, **changes):
    frame = meta['frames'][0]
    return {'kind':'anchor', 'semanticClass':'crosswalk', 'label':'Visible crossing', 'region':BOX,
        'time':{'startS':frame['mediaTimeS'], 'endS':frame['mediaTimeS']}, 'frameIndices':[frame['frameIndex']],
        'visibility':'visible', 'state':'unknown', 'count':{'lower':None,'upper':None},
        'confidence':.8, 'provenance':'observed', **changes}


class Provider:
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [lambda m: [fact(m)]])
        self.requests = []

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        meta = json.loads(body['input'][0]['text'])
        selected = self.outputs.pop(0) if len(self.outputs)>1 else self.outputs[0]
        if isinstance(selected, int):
            return httpx.Response(selected, json={'error':'scripted failure'})
        content = selected(meta)
        output = {'captureId':meta['captureId'],'sourceId':meta['sourceId'], 'prefixSha256':meta['prefixSha256'],
                  'facts': content, 'unknowns':[{'field':'identity','reason':'Not established'}]} if isinstance(content,list) else content
        return httpx.Response(200,json={'id':f'hydrogen-test-{len(self.requests)}','status':'completed','usage':USAGE,
            'steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(output)}]}]})


def setup(tmp_path):
    captures, store = CaptureStore(tmp_path/'captures'), ArtifactStore(tmp_path/'artifacts')
    manifest = captures.create_snapshot_episode('dot-test', png(size=(200,100)), 'image/png', utcnow(),
                                                'https://example.invalid/test-fixture')
    return captures, store, manifest


async def perform(tmp_path, captures, store, manifest, provider, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
        gateway = ModelGateway(GatewayConfig(data_dir=tmp_path/'ledger', max_attempts=1,
            rates_valid_through=date(2030,1,1)), client, api_key='observation-test-secret')
        return await extract(gateway, store, captures, ExtractRequest(captureId=manifest['id'],cameraId=manifest['cameraId'],
            startS=kwargs.pop('start',0),endS=kwargs.pop('end',None)), **kwargs)


def test_snapshot_has_real_gateway_trace_exact_pixels_and_reviewable_provenance(tmp_path):
    captures, store, manifest = setup(tmp_path)
    provider = Provider()
    report = asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    assert report['status']=='completed' and report['manualReview']=='optional_not_reviewed'
    row = report['facts'][0]
    evidence = row['evidence'][0]
    assert evidence['captureId']==manifest['id'] and evidence['sourceId']=='dot-test'
    assert evidence['mediaTimeS']==0 and evidence['frameIndex']==0
    assert evidence['prefixSha256']==manifest['files']['prefix']['sha256']
    assert report['id'] in captures.pins(manifest['id'])
    batch = ObservationBatch.model_validate(store.get('observations', report['batchIds'][0]))
    assert not batch.motionSupported
    assert batch.modelCall.requestId.startswith('hydrogen-test-') and batch.modelCall.outcome=='ok'
    assert evidence['frameSha256'] in batch.modelCall.inputHashes
    assert batch.anchors[0].id == row['id'].replace('ob_','anc_',1)
    assert store.get('observation_evidence',batch.id)['facts']==report['facts']
    assert report['windows'][0]['gatewayRequestId']
    assert provider.requests[0]['input'][2]['type']=='image'
    assert provider.requests[0]['generation_config']['thinking_level']=='low'
    assert store.get('extractions',report['id'])==report
    assert len(store.list('extraction_attempts'))==2
    assert '<img src="data:image/png;base64,' in review_html(store,report)
    assert annotation_template(report)['annotations']==[]


def test_wire_schema_preserves_fixed_discriminator_values():
    schema=WireOutput.model_json_schema()
    assert schema['$defs']['BoxRegion']['properties']['kind']['enum']==['box']
    assert 'const' not in schema['$defs']['BoxRegion']['properties']['kind']


def test_permanent_budget_or_quota_failure_does_not_retry_later_windows(tmp_path,video):
    captures,store,_=setup(tmp_path)
    manifest=video_capture(captures,video)
    provider=Provider([429,lambda m:[fact(m)]])
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    assert len(provider.requests)==1 and report['metrics']['unprocessedWindows']==1
    assert report['status']=='failed' and report['metrics']['attempts']==1


@pytest.mark.parametrize('change', ['capture','source','hash','frame','time','movement','flow','identity'])
def test_invalid_evidence_is_retained_as_failure_and_never_published(tmp_path, change):
    captures, store, manifest = setup(tmp_path)
    def output(m):
        f=fact(m)
        out={'captureId':m['captureId'],'sourceId':m['sourceId'],'prefixSha256':m['prefixSha256'],'facts':[f],'unknowns':[]}
        if change=='capture': out['captureId']='cap_other-0001'
        if change=='source': out['sourceId']='different-camera'
        if change=='hash': out['prefixSha256']='sha256:'+'0'*64
        if change=='frame': f['frameIndices']=[9]
        if change=='time': f['time']={'startS':0,'endS':1}
        if change=='movement': f.update(kind='event',state='crossing',provenance='derived')
        if change=='flow': f['flowPerHour']=400
        if change=='identity': f['persistentIdentity']='same-person'
        return out
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([output])))
    assert report['status']=='failed' and report['metrics']['failures']==1 and not report['batchIds']
    assert report['windows'][0]['modelCall']['outcome']=='invalid_output'
    assert report['windows'][0]['modelCall']['costUsd'] > 0
    assert report['windows'][0]['gatewayRequestId']
    assert not store.list('observations')


@pytest.mark.parametrize('changes', [
    {'kind':'signal','semanticClass':'signal','visibility':'partial','state':'green'},
    {'kind':'count','semanticClass':'vehicle','visibility':'unobservable','count':{'lower':1,'upper':2}},
    {'kind':'count','semanticClass':'vehicle','count':{'lower':5,'upper':3}},
    {'frameIndices':[0.5]},
    {'region':{'kind':'box','x':.9,'y':0,'w':.2,'h':1}},
    {'confidence':float('nan')},
    {'provenance':'generated'},
])
def test_structural_abstention_and_geometry_guards(changes):
    with pytest.raises(ValidationError):
        Fact.model_validate(fact({'frames':[{'frameIndex':0,'mediaTimeS':0}]},**changes))


def test_abstentions_and_provider_failures_are_visible_in_denominators(tmp_path):
    captures,store,manifest=setup(tmp_path)
    def unknown(m):
        return [fact(m,kind='signal',semanticClass='signal',visibility='unobservable',state='unknown'),
                fact(m,kind='count',semanticClass='vehicle',visibility='unobservable')]
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([unknown])))
    assert report['status']=='abstained' and report['metrics']['abstentions']==2
    assert store.get('observations',report['batchIds'][0])['observations']==[]
    failed=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([429])))
    assert failed['status']=='failed' and failed['windows'][0]['modelCall']['outcome']=='quota'
    assert len(store.list('extractions'))==2


def test_reinspection_preserves_capture_remaps_crop_and_is_bounded(tmp_path):
    captures,store,manifest=setup(tmp_path)
    provider=Provider()
    parent=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    ask=ReinspectRequest(focus='anchors',time={'startS':0,'endS':0},roi={'kind':'box','x':.2,'y':.1,'w':.5,'h':.8})
    a=asyncio.run(perform(tmp_path,captures,store,manifest,provider,parent=parent,reinspection=ask))
    e=a['facts'][0]['evidence'][0]
    assert e['roi']['x']==pytest.approx(.25) and e['roi']['y']==pytest.approx(.26)
    assert e['frameSha256']!=e['modelImageSha256']
    assert e['captureId']==parent['captureId'] and e['prefixSha256']==parent['prefixSha256']
    asyncio.run(perform(tmp_path,captures,store,manifest,provider,parent=parent,reinspection=ask))
    with pytest.raises(ArtifactError,match='already used'):
        asyncio.run(perform(tmp_path,captures,store,manifest,provider,parent=parent,reinspection=ask))
    with pytest.raises(ArtifactError,match='recursively'):
        asyncio.run(perform(tmp_path,captures,store,manifest,provider,parent=a,reinspection=ask))
    assert len(provider.requests)==3


def test_wrong_source_and_out_of_prefix_selection_never_calls_model(tmp_path):
    captures,store,manifest=setup(tmp_path)
    provider=Provider()
    with pytest.raises(ArtifactError):
        asyncio.run(perform(tmp_path,captures,store,manifest,provider,end=1))
    from backend.capture import SceneMismatch
    with pytest.raises(SceneMismatch):
        verified_prefix(captures,manifest['id'],'other-camera')
    assert not provider.requests


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg required for real frame-index decoding tests')
    target=tmp_path_factory.mktemp('observation_video')/'fixture.ts'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=6','-t','8',
                    '-c:v','mpeg2video','-f','mpegts',str(target)],capture_output=True,check=True,timeout=30)
    holdout=target.with_name('holdout.ts')
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:size=160x90:rate=6','-t','2',
                    '-c:v','mpeg2video','-f','mpegts',str(holdout)],capture_output=True,check=True,timeout=30)
    return target.read_bytes(), holdout.read_bytes()


def video_capture(captures,video):
    # Both partitions are real decodable fixtures with explicit byte boundaries.
    # Isolation checks still ensure no holdout bytes/timeline are used by extraction.
    prefix,holdout=video
    segment = {'durationS':8, 'bytes':len(prefix), 'sha256':sha256(prefix)}
    held_segment = {'durationS':2, 'bytes':len(holdout), 'sha256':sha256(holdout)}
    return captures._write_episode(camera_id='511ny-32998',source_kind='hls',media_type='video/mp2t',ext='ts',
        prefix_bytes=prefix,holdout_bytes=holdout,segments={'prefix':[segment],'holdout':[held_segment]},
        prefix_duration=8,holdout_duration=2,received=(utcnow(),utcnow()),source_clock='unknown',request={},labels=[])


def test_video_indexes_exact_frames_and_never_reads_suffix_into_model(tmp_path,video):
    captures,store,_=setup(tmp_path)
    manifest=video_capture(captures,video)
    provider=Provider()
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    assert report['status']=='completed' and len(report['windows'])==2
    assert all(w['motionSupported'] for w in report['windows'])
    assert all('held-out-secret' not in json.dumps(r) for r in provider.requests)
    assert manifest['files']['holdout']['sha256'] not in json.dumps(provider.requests)
    assert all(e['mediaTimeS']<8 for f in report['facts'] for e in f['evidence'])
    assert all(e['frameIndex']<manifest['frameCount'] for f in report['facts'] for e in f['evidence'])
    assert len(set(report['batchIds']))==2
    with pytest.raises(ArtifactError):
        asyncio.run(perform(tmp_path,captures,store,manifest,provider,end=9))


def test_event_ownership_and_uncertainty_do_not_double_count():
    from backend.observation_contracts import HydrogenOutput
    manifest={'id':'cap_test-0001','cameraId':'camera-test','files':{'prefix':{'sha256':'sha256:'+'a'*64}}}
    frames=[{'id':f'frame_test-000{i}','frameIndex':i,'mediaTimeS':t,'frameSha256':f'sha256:{i:064x}'} for i,t in enumerate([4.,5.,6.,7.])]
    images=[{'sha256':f['frameSha256'],'roi':{'kind':'box','x':0,'y':0,'w':1,'h':1}} for f in frames]
    meta={'frames':frames}
    def output(start,end,indices):
        return HydrogenOutput(captureId=manifest['id'],sourceId=manifest['cameraId'],prefixSha256=manifest['files']['prefix']['sha256'],
            facts=[fact(meta,kind='event',state='crossing',provenance='derived',semanticClass='pedestrian',
                        time={'startS':start,'endS':end},frameIndices=indices)])
    w=windows(0,8)
    first=accept_facts(output(4,5,[0,1]),manifest,w[0],frames,images,True,[])
    assert first[0]['disposition']=='accepted'
    duplicate=accept_facts(output(4,5,[0,1]),manifest,w[1],frames,images,True,first)
    assert duplicate[0]['disposition']=='duplicate'
    uncertain=accept_facts(output(5,7,[1,3]),manifest,w[1],frames,images,True,first)
    assert uncertain[0]['disposition']=='boundary_uncertain'


def test_reinspection_marks_parent_event_duplicate_instead_of_counting_again(tmp_path,video):
    captures,store,_=setup(tmp_path)
    manifest=video_capture(captures,video)
    def event(meta):
        left,right=meta['frames'][:2]
        return [fact(meta,kind='event',semanticClass='pedestrian',state='crossing',provenance='derived',
                     frameIndices=[left['frameIndex'],right['frameIndex']],
                     time={'startS':left['mediaTimeS'],'endS':right['mediaTimeS']})]
    provider=Provider([event])
    parent=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    ask=ReinspectRequest(focus='events',time={'startS':0,'endS':3},roi={'kind':'box','x':0,'y':0,'w':1,'h':1})
    child=asyncio.run(perform(tmp_path,captures,store,manifest,provider,parent=parent,reinspection=ask,end=3))
    assert child['metrics']['duplicates']==1 and child['metrics']['acceptedFacts']==0
    assert child['facts'][0]['duplicateOf'] in {row['id'] for row in parent['facts']}


@pytest.mark.parametrize('indices, repeated_crop, accepted', [
    ([0, 1], False, False), ([0, 2], False, True), ([0], False, False), ([0, 2], True, False)])
def test_events_require_distinct_pixels_in_the_frames_they_cite(indices, repeated_crop, accepted):
    from backend.observation_contracts import HydrogenOutput
    manifest = {'id':'cap_test-0001', 'cameraId':'camera-test',
                'files':{'prefix':{'sha256':'sha256:'+'a'*64}}}
    frames = [{'id':f'frame_test-000{i}', 'frameIndex':i, 'mediaTimeS':float(i),
               'frameSha256':'sha256:'+digest*64} for i, digest in enumerate(['b', 'b', 'c'])]
    images = [{'sha256':frame['frameSha256'], 'roi':{'kind':'box', 'x':0, 'y':0, 'w':1, 'h':1}}
              for frame in frames]
    if repeated_crop:
        images = [{'sha256':'sha256:'+'d'*64, 'roi':{'kind':'box', 'x':.2, 'y':.2, 'w':.3, 'h':.3}}
                  for _ in frames]
    # Motion elsewhere in a window cannot support an event whose own evidence
    # consists of one image or repeated copies of that image.
    motion = len({frame['frameSha256'] for frame in frames}) >= 2
    output = HydrogenOutput(captureId=manifest['id'], sourceId=manifest['cameraId'],
        prefixSha256=manifest['files']['prefix']['sha256'], facts=[fact({'frames':frames},
            kind='event', state='crossing', provenance='derived', semanticClass='pedestrian',
            time={'startS':float(min(indices)), 'endS':float(max(indices))}, frameIndices=indices)])
    if accepted:
        rows = accept_facts(output, manifest, windows(0, 3)[0], frames, images, motion, [])
        assert rows[0]['disposition'] == 'accepted'
    else:
        with pytest.raises(ValueError, match='Temporal claims require distinct continuous-media observations'):
            accept_facts(output, manifest, windows(0, 3)[0], frames, images, motion, [])


def test_manual_review_reports_errors_and_does_not_hide_model_abstention(tmp_path):
    captures,store,manifest=setup(tmp_path)
    provider=Provider([lambda m:[fact(m,kind='count',semanticClass='vehicle',count={'lower':1,'upper':2}),
                               fact(m,kind='signal',semanticClass='signal',visibility='unobservable')]])
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    frames=report['windows'][0]['frameIds']
    review=ManualReview(**{**annotation_template(report),'reviewer':'fixture-reviewer','reviewedOriginalPixels':True,'annotations':[
        {'kind':'count','semanticClass':'vehicle','time':{'startS':0,'endS':0},'region':BOX,'frameIds':frames,'present':'yes','count':{'lower':4,'upper':4}},
        {'kind':'signal','semanticClass':'signal','time':{'startS':0,'endS':0},'region':BOX,'frameIds':frames,'present':'yes','state':'red'}]})
    result=score_review(store,report,review)
    assert result['counts']=={'count_error':1,'model_abstained':1}
    assert result['extractionMetrics']['abstentions']==1
    with pytest.raises(ArtifactError):
        score_review(store,report,review.model_copy(update={'prefixSha256':'sha256:'+'0'*64}))


def test_api_requires_operator_auth_and_keeps_original_pixels_next_to_facts(tmp_path,monkeypatch):
    captures,store,manifest=setup(tmp_path)
    provider=Provider([lambda m:[fact(m,label='<script>unsafe</script>')]])
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    app=FastAPI()
    app.state.artifacts=store
    app.include_router(router)
    monkeypatch.setenv('WORLD_API_TOKEN','x'*32)
    with TestClient(app) as client:
        path=f'/api/observations/extractions/{report["id"]}'
        assert client.get(path).status_code==401
        auth={'Authorization':'Bearer '+'x'*32}
        assert client.get(path,headers=auth).json()['captureId']==manifest['id']
        page=client.get(path+'/review',headers=auth)
        assert page.status_code==200 and '&lt;script&gt;' in page.text and '<script>' not in page.text
        assert client.get(path+'/frames/frame_not-in-job',headers=auth).status_code==403
        frame=report['windows'][0]['frameIds'][0]
        assert client.get(path+'/frames/'+frame,headers=auth).content==png(size=(200,100))
        assert client.get(path+'/failure-report',headers=auth).json()['humanAccuracy'] is None
        assert client.get(path+'/reviews',headers=auth).json()['status']=='optional_not_reviewed'
        body=manual_review(report).model_dump(mode='json')
        assert client.post(path+'/annotations',json=body).status_code==401
        submitted=client.post(path+'/annotations',json=body,headers=auth)
        assert submitted.status_code==200
        review_id=submitted.json()['id']
        assert client.get(path+'/reviews/'+review_id,headers=auth).json()['sha256']==submitted.json()['sha256']
        assert len(client.get(path+'/reviews',headers=auth).json()['reviews'])==1


def test_cancellation_retains_trace_failure_and_pinned_originals(tmp_path):
    captures,store,manifest=setup(tmp_path)
    async def run():
        started=asyncio.Event()
        async def hanging(request):
            started.set()
            await asyncio.sleep(10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(hanging)) as http:
            gateway=ModelGateway(GatewayConfig(data_dir=tmp_path/'ledger',max_attempts=1,
                rates_valid_through=date(2030,1,1)),http,api_key='test-observation-key')
            task=asyncio.create_task(extract(gateway,store,captures,ExtractRequest(captureId=manifest['id'],cameraId=manifest['cameraId'])))
            await asyncio.wait_for(started.wait(),2)
            task.cancel('private cancellation detail')
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await task
            assert cancelled.value.args == ('private cancellation detail',)
            report=store.list('extractions')[0]
            assert report['status']=='cancelled' and report['metrics']['failures']==1
            request_id = cancelled.value.request_id
            attempt = report['windows'][0]
            assert attempt['gatewayRequestId'] == request_id
            assert attempt['modelCall']['requestId'] == f'gateway:{request_id}'
            ledger = gateway.ledger.get(request_id)
            assert ledger['outcome'] == 'cancelled' and ledger['usage'] is None
            assert 'sha256:' + ledger['input_hash'] in attempt['modelCall']['inputHashes']
            assert all(attempt['modelCall'][field] is None for field in ('inputTokens', 'outputTokens', 'costUsd'))
            assert 'private cancellation detail' not in json.dumps(report)
            assert report['id'] in captures.pins(manifest['id'])
    asyncio.run(run())


def manual_annotation(report, **changes):
    return {'kind':'count','semanticClass':'vehicle','time':{'startS':0,'endS':0},'region':BOX,
            'frameIds':report['windows'][0]['frameIds'],'present':'yes','count':{'lower':4,'upper':4},**changes}


def manual_review(report, **changes):
    return ManualReview.model_validate({**annotation_template(report),'reviewer':'scripted-test-fixture',
        'reviewedOriginalPixels':True,'annotations':[manual_annotation(report)],**changes})


def test_reviews_bind_exact_report_original_frames_and_attestation(tmp_path):
    captures,store,manifest=setup(tmp_path)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider()))
    template=annotation_template(report)
    assert template['annotations']==[] and template['reviewedOriginalPixels'] is False
    with pytest.raises(ValidationError):
        ManualReview.model_validate({**template,'reviewer':'fixture','annotations':[manual_annotation(report)]})
    review=manual_review(report)
    result=score_review(store,report,review)
    assert verified_review(result,report)==result
    assert result['annotation']['reviewMode']=='model_assisted'
    with pytest.raises(ArtifactError,match='exact capture'):
        score_review(store,{**report,'status':'changed'},review)
    with pytest.raises(ArtifactError,match='original frames'):
        score_review(store,report,manual_review(report,annotations=[manual_annotation(report,frameIds=['frame_other-0001'])]))
    with pytest.raises(ArtifactError,match='Duplicate'):
        score_review(store,report,manual_review(report,annotations=[manual_annotation(report)]*2))
    with pytest.raises(ArtifactError,match='content hash'):
        verified_review({**result,'counts':{'matched':100}},report)
    # A review cannot silently transfer to another extraction of the same pixels.
    other=asyncio.run(perform(tmp_path,captures,store,manifest,Provider()))
    with pytest.raises(ArtifactError):
        score_review(store,other,review)


@pytest.mark.parametrize('change', [
    {'kind':'signal','semanticClass':'signal'},
    {'present':'unknown'},
    {'kind':'event','state':'crossing','semanticClass':'pedestrian'},
    {'frameIds':[]},
    {'time':{'startS':0,'endS':1}},
])
def test_manual_annotation_semantics_do_not_invent_unsupported_truth(tmp_path,change):
    captures,store,manifest=setup(tmp_path)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider()))
    with pytest.raises(ValidationError):
        manual_review(report,annotations=[manual_annotation(report,**change)])


def test_count_comparison_requires_matching_region_and_unknown_stays_unscored(tmp_path):
    captures,store,manifest=setup(tmp_path)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([
        lambda meta:[fact(meta,kind='count',semanticClass='vehicle',count={'lower':1,'upper':2})]])))
    shifted={**BOX,'x':.12}
    result=score_review(store,report,manual_review(report,annotations=[manual_annotation(report,region=shifted)]))
    assert result['counts']=={'count_region_mismatch_unscored':1}
    unknown=score_review(store,report,manual_review(report,annotations=[manual_annotation(report,count={'lower':None,'upper':None})]))
    assert unknown['counts']=={'human_unknown':1}


@pytest.mark.parametrize('lower,upper,outcome', [(0,0,'correct_absence'),(0,2,'model_abstained'),(1,2,'false_positive')])
def test_reviewed_absence_respects_zero_and_uncertain_model_count(tmp_path,lower,upper,outcome):
    captures,store,manifest=setup(tmp_path)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([
        lambda meta:[fact(meta,kind='count',semanticClass='vehicle',count={'lower':lower,'upper':upper})]])))
    annotation=manual_annotation(report,present='no',count={'lower':None,'upper':None})
    assert score_review(store,report,manual_review(report,annotations=[annotation]))['counts']=={outcome:1}


def test_reviewed_absence_never_counts_failed_extraction_as_correct(tmp_path):
    captures,store,manifest=setup(tmp_path)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([429])))
    annotation=manual_annotation(report,present='no',count={'lower':None,'upper':None})
    result=score_review(store,report,manual_review(report,annotations=[annotation]))
    assert result['counts']=={'model_failed_or_unprocessed':1}
    assert result['coverage']['scoredComparisons']==0


def test_offline_review_export_is_empty_safe_and_never_overwrites_human_work(tmp_path,capsys):
    captures,store,manifest=setup(tmp_path)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider()))
    packet=tmp_path/'packet'
    args=['--artifact-dir',str(store.root),'--extraction',report['id'],'--export-dir',str(packet)]
    assert review_main(args)==0
    assert json.loads((packet/'annotations.template.json').read_text())['annotations']==[]
    assert json.loads((packet/'failures.json').read_text())['humanAccuracy'] is None
    html=(packet/'review.html').read_text()
    assert html.index('Original evidence') < html.index('Model facts and evidence links')
    assert not store.list('reviews')
    (packet/'annotations.template.json').write_text('human work in progress')
    assert review_main(args)==1
    assert (packet/'annotations.template.json').read_text()=='human work in progress'
    annotation_file=tmp_path/'human-fixture.json'
    annotation_file.write_text(manual_review(report).model_dump_json())
    assert review_main(['--artifact-dir',str(store.root),'--extraction',report['id'],'--annotations',str(annotation_file)])==0
    assert len(store.list('reviews'))==1


def test_failure_report_preserves_failed_abstained_and_unprocessed_windows(tmp_path,video):
    captures,store,_=setup(tmp_path)
    manifest=video_capture(captures,video)
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([429])))
    detail=failure_report(report)
    assert detail['plannedWindows']==2 and detail['metrics']['unprocessedWindows']==1
    assert detail['metrics']['failures']==1 and detail['windows'][0]['gatewayRequestId']
    assert detail['humanAccuracy'] is None


def test_invalid_domain_output_and_abstention_never_qualify_but_success_does(tmp_path):
    captures,store,manifest=setup(tmp_path)
    async def run():
        provider=Provider([lambda meta:[fact(meta,frameIndices=[99])],lambda meta:[],lambda meta:[fact(meta)]])
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as client:
            gateway=ModelGateway(GatewayConfig(data_dir=tmp_path/'ledger',max_attempts=1,
                rates_valid_through=date(2030,1,1)),client,api_key='fixture-secret')
            request=ExtractRequest(captureId=manifest['id'],cameraId=manifest['cameraId'])
            invalid=await extract(gateway,store,captures,request)
            assert invalid['status']=='failed' and gateway.ledger.attempts()[0]['qualifying']==0
            abstained=await extract(gateway,store,captures,request)
            assert abstained['status']=='abstained' and gateway.ledger.attempts()[0]['qualifying']==0
            assert abstained['windows'][0]['acceptanceBinding']
            accepted=await extract(gateway,store,captures,request)
            assert accepted['status']=='completed' and gateway.ledger.attempts()[0]['qualifying']==1
            context=json.loads(gateway.ledger.attempts()[0]['context'])
            assert context['episode_id']==manifest['id'] and context['source_id']==manifest['cameraId']
            assert context['attempt_id']==accepted['windows'][0]['attemptId']
    asyncio.run(run())




@pytest.mark.parametrize('abstention_first', [False, True])
def test_negative_review_checks_all_accepted_candidates_before_abstentions(tmp_path, abstention_first):
    captures,store,manifest=setup(tmp_path)
    def rows(meta):
        accepted=[fact(meta,kind='signal',semanticClass='signal',state='red',region={**BOX,'w':.25}),
                  fact(meta,kind='signal',semanticClass='signal',state='green',region={**BOX,'w':.2})]
        abstention=fact(meta,kind='signal',semanticClass='signal',visibility='unobservable')
        return [abstention,*accepted] if abstention_first else [*accepted,abstention]
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([rows])))
    review=ManualReview(**{**annotation_template(report),'reviewer':'synthetic-regression-fixture','reviewedOriginalPixels':True,'annotations':[
        {'kind':'signal','semanticClass':'signal','time':{'startS':0,'endS':0},'region':BOX,'frameIds':report['windows'][0]['frameIds'],'present':'no'}]})
    result=score_review(store,report,review)
    comparison=result['comparisons'][0]
    accepted_ids={row['id'] for row in report['facts'] if row['disposition']=='accepted'}
    assert comparison['outcome']=='false_positive'
    assert comparison['factId'] in accepted_ids and set(comparison['factIds'])==accepted_ids
    assert result['unmatchedAcceptedFacts']==[]


@pytest.mark.parametrize('abstained,expected', [(True,'model_abstained'),(False,'correct_absence')])
def test_negative_review_keeps_abstention_distinct_from_absence(tmp_path, abstained, expected):
    captures,store,manifest=setup(tmp_path)
    provider=Provider([lambda meta:[fact(meta,kind='signal',semanticClass='signal',visibility='unobservable')]
                       if abstained else []])
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    review=ManualReview(**{**annotation_template(report),'reviewer':'synthetic-regression-fixture','reviewedOriginalPixels':True,'annotations':[
        {'kind':'signal','semanticClass':'signal','time':{'startS':0,'endS':0},'region':BOX,'frameIds':report['windows'][0]['frameIds'],'present':'no'}]})
    assert score_review(store,report,review)['comparisons'][0]['outcome']==expected


@pytest.mark.parametrize('human_count,expected', [({},'human_unknown'),
    ({'lower':1,'upper':1},'count_interval_overlap'),({'lower':4,'upper':4},'count_error')])
def test_count_review_requires_human_quantity_and_preserves_presence(tmp_path, human_count, expected):
    captures,store,manifest=setup(tmp_path)
    provider=Provider([lambda meta:[fact(meta,kind='count',semanticClass='vehicle',count={'lower':1,'upper':2})]])
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    review=ManualReview(**{**annotation_template(report),'reviewer':'synthetic-regression-fixture','reviewedOriginalPixels':True,'annotations':[
        {'kind':'count','semanticClass':'vehicle','time':{'startS':0,'endS':0},'region':BOX,
         'frameIds':report['windows'][0]['frameIds'],'present':'yes','count':human_count}]})
    comparison=score_review(store,report,review)['comparisons'][0]
    assert comparison['outcome']==expected
    assert comparison['presenceOutcome']=='matched'


@pytest.mark.parametrize('model_case,presence', [('missing','missed'),
    ('unknown','model_abstained'),('partial_overlap','region_error')])
def test_count_review_without_human_quantity_keeps_presence_errors(tmp_path, model_case, presence):
    captures,store,manifest=setup(tmp_path)
    def rows(meta):
        if model_case=='missing':
            return []
        return [fact(meta,kind='count',semanticClass='vehicle',
            count={} if model_case=='unknown' else {'lower':1,'upper':2},
            region={**BOX,'w':.1} if model_case=='partial_overlap' else BOX)]
    report=asyncio.run(perform(tmp_path,captures,store,manifest,Provider([rows])))
    review=ManualReview(**{**annotation_template(report),'reviewer':'synthetic-regression-fixture','reviewedOriginalPixels':True,'annotations':[
        {'kind':'count','semanticClass':'vehicle','time':{'startS':0,'endS':0},'region':BOX,'frameIds':report['windows'][0]['frameIds'],'present':'yes'}]})
    result=score_review(store,report,review)
    assert result['counts']=={'human_unknown':1}
    assert result['comparisons'][0]['presenceOutcome']==presence


@pytest.mark.parametrize('completed_windows', [0, 1])
def test_cancel_before_admission_never_borrows_a_previous_window_trace(tmp_path, video, completed_windows):
    captures, store, _ = setup(tmp_path)
    manifest = video_capture(captures, video)
    provider = Provider()

    async def run():
        waiting = asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handler)) as http:
            gateway = ModelGateway(GatewayConfig(data_dir=tmp_path/'ledger', max_attempts=1,
                rates_valid_through=date(2030, 1, 1)), http, api_key='test-observation-key')
            acquire = gateway.slots.acquire
            admissions = 0

            async def blocked_admission():
                nonlocal admissions
                if admissions == completed_windows:
                    waiting.set()
                    await asyncio.Semaphore(0).acquire()
                admissions += 1
                return await acquire()

            gateway.slots.acquire = blocked_admission
            task = asyncio.create_task(extract(gateway, store, captures,
                ExtractRequest(captureId=manifest['id'], cameraId=manifest['cameraId'])))
            await asyncio.wait_for(waiting.wait(), 5)
            completed = [record for record in store.list('extraction_attempts') if record['status'] == 'ok']
            task.cancel('private cancellation detail')
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await task
            assert cancelled.value.args == ('private cancellation detail',)
            assert getattr(cancelled.value, 'request_id', None) is None
            report = store.list('extractions')[0]
            assert report['status'] == 'cancelled'
            assert report['windows'][:-1] == completed
            assert len(report['batchIds']) == completed_windows
            assert len(provider.requests) == len(gateway.ledger.attempts()) == completed_windows
            attempt = report['windows'][-1]
            assert attempt['gatewayRequestId'] is None
            assert all(attempt['modelCall'][field] is None
                       for field in ('requestId', 'inputTokens', 'outputTokens', 'costUsd'))
            for previous in completed:
                assert 'sha256:' + previous['inputHash'] not in attempt['modelCall']['inputHashes']
            assert 'private cancellation detail' not in json.dumps(report)

    asyncio.run(run())
