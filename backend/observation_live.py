"""Explicit capture and inference steps: retries reuse saved original media."""
import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
import httpx

from .artifacts import ArtifactStore, utcnow
from .capture import CaptureStore
from .capture_api import Recorders
from .gateway import GatewayConfig, ModelGateway
from .observation_contracts import ExtractRequest
from .observation_review import annotation_template, review_html
from .observations import extract
from .pipeline_live import snapshot

ROOT = Path(__file__).resolve().parent.parent


async def capture(args):
    captures = CaptureStore(args.root/'captures')
    recorders = Recorders()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                headers={'User-Agent':'GothamForge/0.1 evidence-verification'}) as http:
        try:
            shot = await snapshot(http, captures, args.snapshot_camera)
            print(json.dumps({'captured':'snapshot','captureId':shot['manifest']['id']}),flush=True)
            recorder = recorders.start(args.hls_camera,http)
            async with asyncio.timeout(45):
                while recorder.buffered_seconds() < args.seconds:
                    await asyncio.sleep(.25)
            async with recorder.lock:
                clip = await asyncio.to_thread(captures.create_hls_episode,recorder,args.seconds,args.seconds)
            print(json.dumps({'captured':'continuous','captureId':clip['id'],
                              'prefixSeconds':clip['files']['prefix']['durationS'],'frames':clip['frameCount']}),flush=True)
        finally:
            await recorders.stop_all()
    report = {'createdAt':utcnow().isoformat(), 'source':'real_public_camera_capture',
              'root':str(args.root.resolve()), 'captures':[shot['manifest']['id'],clip['id']],
              'snapshotCamera':shot['camera']['name'],'hlsCamera':args.hls_camera}
    target=args.root/'capture-selection.json'
    target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return {'savedSelection':str(target)}


async def run_saved(args):
    selection=json.loads(args.run_saved.read_text(encoding='utf-8'))
    root=Path(selection['root'])
    captures,store=CaptureStore(root/'captures'),ArtifactStore(root/'artifacts')
    config=GatewayConfig.from_environment()
    # One provider attempt per window; all normal durable/global stage limits apply.
    config=config.model_copy(update={'max_attempts':1})
    summary={'startedAt':utcnow().isoformat(),'selection':str(args.run_saved.resolve()),'runs':[]}
    target=root/f"verification-{utcnow().strftime('%Y%m%dT%H%M%S')}.json"
    async with httpx.AsyncClient(follow_redirects=False) as http:
        gateway=ModelGateway(config,http)
        summary['budgetBefore']=gateway.ledger.budget_status()
        for capture_id in selection['captures']:
            manifest=captures.get(capture_id)
            report=await extract(gateway,store,captures,ExtractRequest(captureId=capture_id,cameraId=manifest['cameraId']))
            (root/f"{report['id']}.html").write_text(review_html(store,report),encoding='utf-8')
            (root/f"{report['id']}-annotations.json").write_text(json.dumps(annotation_template(report),indent=2),encoding='utf-8')
            summary['runs'].append({'captureId':capture_id,'extractionId':report['id'],'status':report['status'],
                'metrics':report['metrics'],'gatewayRequestIds':[w.get('gatewayRequestId') for w in report['windows']]})
            summary['budgetAfter']=gateway.ledger.budget_status()
            target.write_text(json.dumps(summary,indent=2),encoding='utf-8')
            print(json.dumps(summary['runs'][-1]),flush=True)
    return {'verification':str(target),'runs':summary['runs'],
            'accountingDeltaUsd':round(summary['budgetAfter']['charged_usd']-summary['budgetBefore']['charged_usd'],6)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--capture-live',action='store_true',help='Record one real snapshot and one bounded continuous clip; no model calls')
    modes.add_argument('--run-saved',type=Path,help='Make paid Hydrogen calls using exactly this saved capture selection')
    parser.add_argument('--env-file',type=Path,help='Existing local environment file, never printed or copied')
    parser.add_argument('--root',type=Path,default=ROOT/'var'/'observation-verification')
    parser.add_argument('--snapshot-camera')
    parser.add_argument('--hls-camera',default='511ny-32998')
    parser.add_argument('--seconds',type=float,default=8)
    args=parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file,override=False)
    if not args.capture_live and not args.run_saved:
        print('No capture or model calls. Choose --capture-live or --run-saved PATH explicitly.')
        return
    if not 1 <= args.seconds <= 12:
        parser.error('Development capture must be between 1 and 12 seconds.')
    print(json.dumps(asyncio.run(capture(args) if args.capture_live else run_saved(args)),indent=2))


if __name__=='__main__':
    main()
