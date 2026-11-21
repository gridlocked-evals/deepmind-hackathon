import { useEffect, useRef, useState } from 'react';
import type Hls from 'hls.js';
import type { ImageRegion } from '../contracts';
import { apiUrl, mediaPath } from './api';
import type { CaptureManifest } from './api';

export interface ReviewPoint { time: number; region: ImageRegion | null; revision: number }

export function CapturedMedia({ capture, review }: { capture: CaptureManifest; review: ReviewPoint | null }) {
  const video = useRef<HTMLVideoElement>(null);
  const requested = useRef(review);
  requested.current = review;
  const [error, setError] = useState('');
  const [ratio, setRatio] = useState(16 / 9);
  const firstIndex = capture.media.findIndex(item => item.partition === 'prefix');
  useEffect(() => {
    if (capture.modality !== 'video' || !video.current) return;
    const element = video.current;
    const url = apiUrl(`/api/captures/episodes/${encodeURIComponent(capture.captureId)}/index.m3u8`);
    let player: Hls | null = null;
    let disposed = false;
    const load = () => player?.startLoad();
    const loaded = () => {
      if (element.videoWidth && element.videoHeight) setRatio(element.videoWidth / element.videoHeight);
      if (requested.current) element.currentTime = requested.current.time;
    };
    element.addEventListener('play', load);
    element.addEventListener('loadedmetadata', loaded);
    void import('hls.js').then(({ default: HlsEngine }) => {
      if (disposed) return;
      if (HlsEngine.isSupported()) {
        player = new HlsEngine({ maxBufferLength: 12, maxMaxBufferLength: 20 });
        player.on(HlsEngine.Events.ERROR, (_event, info) => { if (info.fatal) setError('This browser could not play the retained clip. Open an original segment below.'); });
        player.loadSource(url);
        player.attachMedia(element);
        player.on(HlsEngine.Events.MANIFEST_PARSED, () => { if (requested.current) player?.startLoad(requested.current.time); });
      } else if (element.canPlayType('application/vnd.apple.mpegurl')) element.src = url;
      else setError('Retained video playback is unavailable in this browser. Original segments remain accessible below.');
    }).catch(() => { if (!disposed) setError('The retained-video player could not load. Original segments remain accessible below.'); });
    return () => {
      disposed = true;
      element.pause();
      element.removeEventListener('play', load);
      element.removeEventListener('loadedmetadata', loaded);
      player?.destroy();
      element.removeAttribute('src');
      element.load();
    };
  }, [capture]);
  useEffect(() => {
    const element = video.current;
    if (element && review) {
      element.pause();
      if (element.readyState > 0) element.currentTime = review.time;
    }
  }, [review]);
  return <div className="evidence-original">
    <div className="evidence-media-frame" style={{ aspectRatio: ratio }}>
      {capture.modality === 'snapshot'
        ? <img src={apiUrl(mediaPath(capture.captureId, firstIndex))} alt="Retained original camera snapshot" onLoad={event => setRatio(event.currentTarget.naturalWidth / event.currentTarget.naturalHeight)} onError={() => setError('The retained snapshot could not be loaded. Open the original file below.')} />
        : <video ref={video} controls muted playsInline preload="metadata" aria-label="Retained original camera video" />}
      {review?.region && capture.modality === 'snapshot' && <span className="evidence-roi" aria-label="Referenced region in original image" style={{
        left: `${review.region.x * 100}%`, top: `${review.region.y * 100}%`, width: `${review.region.width * 100}%`, height: `${review.region.height * 100}%`,
      }} />}
    </div>
    {error && <p className="evidence-inline-error" role="status">{error}</p>}
    <p className="evidence-caption">Retained original {capture.modality === 'snapshot' ? 'image' : 'video'}{review ? ` · ${capture.modality === 'video' ? 'approximate seek to' : 'reference at'} ${review.time.toFixed(3)} s` : ''}. Source exposure time {capture.sourceClock === 'unknown' ? 'unknown' : 'unverified'}.</p>
    {review?.region && capture.modality === 'video' && <p className="evidence-caption">Referenced region: x {(review.region.x * 100).toFixed(1)}%, y {(review.region.y * 100).toFixed(1)}%, width {(review.region.width * 100).toFixed(1)}%, height {(review.region.height * 100).toFixed(1)}%. Video seeking does not guarantee the exact model frame.</p>}
    <details className="evidence-file-list"><summary>Open original {capture.modality === 'snapshot' ? 'file' : 'segments'}</summary><ul>{capture.media.map((item, index) => item.partition === 'prefix' && <li key={index}>
      <a href={apiUrl(mediaPath(capture.captureId, index))} target="_blank" rel="noreferrer">{capture.modality === 'snapshot' ? 'Snapshot image' : `Segment ${index + 1} · ${item.startSeconds.toFixed(3)}–${item.endSeconds.toFixed(3)} s`}</a>
    </li>)}</ul></details>
  </div>;
}
