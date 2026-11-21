import { useEffect, useRef, useState } from 'react';
import type Hls from 'hls.js';
import { ExternalLink, Play, Radio, RefreshCw, VideoOff } from 'lucide-react';
import type { CameraSource } from '../types';
import { directStreamUrl, mediaUrl } from './cameras';
import './media.css';

type FeedStatus = 'connecting' | 'playing' | 'buffering' | 'paused' | 'unavailable' | 'image';
interface Props { camera: CameraSource; onStatus?: (status: string) => void; compact?: boolean }

export function LiveFeed({ camera, onStatus, compact = false }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const callback = useRef(onStatus);
  callback.current = onStatus;
  const [status, setStatus] = useState<FeedStatus>('connecting');
  const [retry, setRetry] = useState(0);
  const [imageSrc, setImageSrc] = useState('');
  const [imageCameraId, setImageCameraId] = useState('');
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const imagePending = useRef(false);
  const imageTimeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => { callback.current?.(status); }, [status]);

  useEffect(() => {
    setStatus('connecting');
    setLoadedAt(null);
    if (camera.kind !== 'snapshot') return;
    let disposed = false;
    imagePending.current = false;
    const refresh = () => {
      if (disposed || document.hidden || imagePending.current) return;
      imagePending.current = true;
      try {
        const url = new URL(mediaUrl(camera.playbackUrl), document.baseURI);
        url.searchParams.set('t', String(Date.now()));
        setImageCameraId(camera.id);
        setImageSrc(url.href);
        clearTimeout(imageTimeout.current);
        imageTimeout.current = setTimeout(() => {
          if (!disposed) { imagePending.current = false; setStatus('unavailable'); }
        }, 12000);
      } catch { imagePending.current = false; setStatus('unavailable'); }
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    const visible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => {
      disposed = true; clearInterval(timer); clearTimeout(imageTimeout.current);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [camera.id, camera.kind, camera.playbackUrl, retry]);

  useEffect(() => {
    if (camera.kind !== 'hls' || !videoRef.current) return;
    const video = videoRef.current;
    const direct = directStreamUrl(camera.id);
    const sources = [...new Set([mediaUrl(camera.playbackUrl), direct].filter((url): url is string => !!url))];
    let disposed = false, index = 0, hls: Hls | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let resume = true, recoveries = 0, generation = 0;
    const stop = () => { clearTimeout(watchdog); hls?.destroy(); hls = undefined; video.pause(); video.removeAttribute('src'); video.load(); };
    const play = () => {
      if (disposed || document.hidden) return;
      void video.play().catch(() => { if (!disposed) { clearTimeout(watchdog); setStatus('paused'); } });
    };
    const fail = () => {
      if (disposed) return;
      if (index + 1 < sources.length) { index += 1; void connect(); }
      else { stop(); setStatus('unavailable'); }
    };
    const armWatchdog = () => {
      clearTimeout(watchdog);
      if (!document.hidden) watchdog = setTimeout(fail, 12000);
    };
    const connect = async () => {
      const attempt = ++generation;
      stop(); recoveries = 0; setStatus('connecting'); armWatchdog();
      try {
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = sources[index]; video.load(); play();
        } else {
          const { default: HlsPlayer } = await import('hls.js');
          if (disposed || attempt !== generation) return;
          if (!HlsPlayer.isSupported()) { clearTimeout(watchdog); setStatus('unavailable'); return; }
          hls = new HlsPlayer({ lowLatencyMode: true, liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5,
            maxBufferLength: 10, backBufferLength: 0, maxLiveSyncPlaybackRate: 1.25,
            manifestLoadingTimeOut: 8000, manifestLoadingMaxRetry: 0,
            levelLoadingTimeOut: 8000, levelLoadingMaxRetry: 1,
            fragLoadingTimeOut: 10000, fragLoadingMaxRetry: 1 });
          hls.on(HlsPlayer.Events.MANIFEST_PARSED, play);
          hls.on(HlsPlayer.Events.ERROR, (_event, data) => {
            if (!data.fatal || disposed || attempt !== generation) return;
            if (data.type === HlsPlayer.ErrorTypes.MEDIA_ERROR && recoveries++ < 1) { hls?.recoverMediaError(); armWatchdog(); }
            else fail();
          });
          hls.attachMedia(video); hls.loadSource(sources[index]);
          if (document.hidden) hls.stopLoad();
        }
      } catch { if (!disposed && attempt === generation) fail(); }
    };
    const playing = () => { clearTimeout(watchdog); if (!disposed) setStatus('playing'); };
    const waiting = () => { if (!disposed && !document.hidden && !video.paused) { setStatus('buffering'); armWatchdog(); } };
    const error = () => { if (!disposed && video.getAttribute('src')) fail(); };
    const paused = () => { if (!disposed && !document.hidden && video.currentTime > 0) { clearTimeout(watchdog); setStatus('paused'); } };
    const visibility = () => {
      if (document.hidden) { resume = !video.paused || video.readyState < 2; clearTimeout(watchdog); video.pause(); hls?.stopLoad(); }
      else if (resume) { hls?.startLoad(-1); armWatchdog(); play(); }
    };
    video.addEventListener('playing', playing); video.addEventListener('waiting', waiting);
    video.addEventListener('error', error); video.addEventListener('pause', paused);
    document.addEventListener('visibilitychange', visibility);
    void connect();
    return () => {
      disposed = true; generation += 1;
      video.removeEventListener('playing', playing); video.removeEventListener('waiting', waiting);
      video.removeEventListener('error', error); video.removeEventListener('pause', paused);
      document.removeEventListener('visibilitychange', visibility); stop();
    };
  }, [camera.id, camera.kind, camera.playbackUrl, retry]);

  const timestamp = loadedAt ? new Date(loadedAt).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : null;
  const active = status === 'playing' || status === 'image';
  return <div className={`feed-player${compact ? ' feed-compact' : ''}`}>
    <div className="feed-screen">
      {camera.kind === 'hls'
        ? <video ref={videoRef} autoPlay muted playsInline controls preload="none" aria-label={`Live roadway source: ${camera.name}`} />
        : <img key={camera.id} src={imageCameraId === camera.id ? imageSrc || undefined : undefined} alt={`Latest NYC DOT image at ${camera.name}`} onLoad={() => {
          clearTimeout(imageTimeout.current); imagePending.current = false;
          setLoadedAt(new Date().toISOString()); setStatus('image');
        }} onError={() => { clearTimeout(imageTimeout.current); imagePending.current = false; setStatus('unavailable'); }} />}
      <div className={`feed-badge${active ? ' feed-active' : ''}`}><span />{camera.kind === 'snapshot' ? 'STREET IMAGE' : status === 'playing' ? 'LIVE SOURCE' : status === 'buffering' ? 'BUFFERING' : 'CAMERA SOURCE'}</div>
      {(status === 'connecting' || status === 'unavailable' || status === 'paused') && <div className="feed-message">
        {status === 'connecting' ? <><Radio size={21} className="feed-pulse" /><strong>Connecting to camera</strong><span>{camera.provider}</span></>
          : status === 'paused' ? <><Play size={22} /><strong>Playback paused</strong><button onClick={() => void videoRef.current?.play().catch(() => setStatus('unavailable'))}><Play size={12} />Play source</button></>
            : <><VideoOff size={22} /><strong>Camera unavailable</strong><span>The public source may be offline.</span><button onClick={() => setRetry(value => value + 1)}><RefreshCw size={12} />Retry connection</button></>}
      </div>}
    </div>
    <div className="feed-caption"><span>{camera.kind === 'snapshot' ? timestamp ? `Image loaded ${timestamp} ET` : 'Periodic image · waiting for source' : 'Public live source · broadcast delay unknown'}</span>
      <a href={camera.sourceUrl} target="_blank" rel="noreferrer" aria-label={`Open official source for ${camera.name}`}>{camera.provider}<ExternalLink size={10} /></a>
    </div>
  </div>;
}

export default LiveFeed;
