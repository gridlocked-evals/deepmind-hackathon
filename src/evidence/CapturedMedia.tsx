import { useEffect, useRef, useState } from 'react';
import type Hls from 'hls.js';
import type { Capture } from '../contracts';
import type { Box } from './api';
import { apiUrl, mediaPath } from './api';

export interface ReviewPoint { time: number; region: Box | null; revision: number }

export function CapturedMedia({ capture, review }: { capture: Capture; review: ReviewPoint | null }) {
  const video = useRef<HTMLVideoElement>(null);
  const requested = useRef(review);
  requested.current = review;
  const [error, setError] = useState('');
  const [ratio, setRatio] = useState(16 / 9);
  useEffect(() => {
    if (capture.sourceKind !== 'hls' || !video.current) return;
    const element = video.current;
    const url = apiUrl(`/api/captures/${encodeURIComponent(capture.id)}/index.m3u8`);
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
      {capture.sourceKind === 'snapshot'
        ? <img src={apiUrl(mediaPath(capture.id))} alt="Retained original camera snapshot" onLoad={event => setRatio(event.currentTarget.naturalWidth / event.currentTarget.naturalHeight)} onError={() => setError('The retained snapshot could not be loaded. Open the original file below.')} />
        : <video ref={video} controls muted playsInline preload="metadata" aria-label="Retained original camera video" />}
      {review?.region && capture.sourceKind === 'snapshot' && <span className="evidence-roi" aria-label="Referenced region in original image" style={{
        left: `${review.region.x * 100}%`, top: `${review.region.y * 100}%`, width: `${review.region.w * 100}%`, height: `${review.region.h * 100}%`,
      }} />}
    </div>
    {error && <p className="evidence-inline-error" role="status">{error}</p>}
    <p className="evidence-caption">Retained original {capture.sourceKind === 'snapshot' ? 'image' : 'hls'}{review ? ` · ${capture.sourceKind === 'hls' ? 'approximate seek to' : 'reference at'} ${review.time.toFixed(3)} s` : ''}. Source exposure time {capture.captureClock === 'unknown' ? 'unknown' : 'unverified'}.</p>
    {review?.region && capture.sourceKind === 'hls' && <p className="evidence-caption">Referenced region: x {(review.region.x * 100).toFixed(1)}%, y {(review.region.y * 100).toFixed(1)}%, width {(review.region.w * 100).toFixed(1)}%, height {(review.region.h * 100).toFixed(1)}%. Video seeking does not guarantee the exact model frame.</p>}
    <p className="evidence-file-list"><a href={apiUrl(mediaPath(capture.id))} target="_blank" rel="noreferrer">Open retained original prefix file</a></p>
  </div>;
}
