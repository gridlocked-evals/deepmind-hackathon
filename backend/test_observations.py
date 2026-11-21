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

from backend.artifacts import ArtifactError, ArtifactStore, utcnow
from backend.capture import CaptureStore
from backend.contracts import ObservationBatch
from backend.gateway import GatewayConfig, ModelGateway
from backend.observation_api import router
from backend.observation_contracts import ExtractRequest, Fact, ManualReview, ReinspectRequest, WireOutput
from backend.observation_media import index_prefix, verified_prefix
from backend.observation_review import annotation_template, review_html, score_review
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
    assert report['status']=='completed' and report['manualReview']=='pending_human_annotations'
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
    return target.read_bytes()


def video_capture(captures,video):
    return captures._write_episode(camera_id='511ny-32998',source_kind='hls',media_type='video/mp2t',ext='ts',
        prefix_bytes=video,holdout_bytes=b'held-out-secret-never-visible',segments={'prefix':[],'holdout':[]},
        prefix_duration=8,holdout_duration=2,received=(utcnow(),utcnow()),source_clock='unknown',request={},labels=[])


def test_video_indexes_exact_frames_and_never_reads_suffix_into_model(tmp_path,video):
    captures,store,_=setup(tmp_path)
    manifest=video_capture(captures,video)
    provider=Provider()
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    assert report['status']=='completed' and len(report['windows'])==2
    assert all(w['motionSupported'] for w in report['windows'])
    assert all('held-out-secret' not in json.dumps(r) for r in provider.requests)
    assert all(e['mediaTimeS']<8 for f in report['facts'] for e in f['evidence'])
    assert all(e['frameIndex']<manifest['frameCount'] for f in report['facts'] for e in f['evidence'])
    assert len(set(report['batchIds']))==2
    with pytest.raises(ArtifactError):
        asyncio.run(perform(tmp_path,captures,store,manifest,provider,end=9))


def test_event_ownership_and_uncertainty_do_not_double_count():
    from backend.observation_contracts import HydrogenOutput
    manifest={'id':'cap_test-0001','cameraId':'camera-test','files':{'prefix':{'sha256':'sha256:'+'a'*64}}}
    frames=[{'id':f'frame_test-000{i}','frameIndex':i,'mediaTimeS':t,'frameSha256':'sha256:'+'b'*64} for i,t in enumerate([4.,5.,6.,7.])]
    images=[{'sha256':'sha256:'+'b'*64,'roi':{'kind':'box','x':0,'y':0,'w':1,'h':1}}]*4
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


def test_manual_review_reports_errors_and_does_not_hide_model_abstention(tmp_path):
    captures,store,manifest=setup(tmp_path)
    provider=Provider([lambda m:[fact(m,kind='count',semanticClass='vehicle',count={'lower':1,'upper':2}),
                               fact(m,kind='signal',semanticClass='signal',visibility='unobservable')]])
    report=asyncio.run(perform(tmp_path,captures,store,manifest,provider))
    review=ManualReview(**{**annotation_template(report),'reviewer':'fixture-reviewer','annotations':[
        {'kind':'count','semanticClass':'vehicle','time':{'startS':0,'endS':0},'region':BOX,'present':'yes','count':{'lower':4,'upper':4}},
        {'kind':'signal','semanticClass':'signal','time':{'startS':0,'endS':0},'region':BOX,'present':'yes','state':'red'}]})
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
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            report=store.list('extractions')[0]
            assert report['status']=='cancelled' and report['metrics']['failures']==1
            assert report['windows'][0]['gatewayRequestId']
            assert report['windows'][0]['modelCall']['requestId'].startswith('gateway:')
            assert report['id'] in captures.pins(manifest['id'])
    asyncio.run(run())
