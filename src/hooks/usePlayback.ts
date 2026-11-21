import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';

export function usePlayback(scenario: ScenarioBundle) {
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [time, setTime] = useState(0);
  const timeRef = useRef(0);
  const frameRef = useRef(sampleScenario(scenario, 0));
  const scenarioRef = useRef(scenario);
  if (scenarioRef.current !== scenario) {
    scenarioRef.current = scenario;
    timeRef.current = 0;
    frameRef.current = sampleScenario(scenario, 0);
  }
  useLayoutEffect(() => {
    setTime(0);
    setPlaying(true);
  }, [scenario]);
  const seek = useCallback((next: number) => {
    timeRef.current = Math.max(0, Math.min(scenario.duration, Number.isFinite(next) ? next : 0));
    frameRef.current = sampleScenario(scenario, timeRef.current);
    setTime(timeRef.current);
    if (scenario.playbackMode === 'once' && timeRef.current >= scenario.duration) setPlaying(false);
  }, [scenario]);

  useEffect(() => {
    // Pressing play after arrival replays from the start; arriving itself never loops.
    if (playing && scenario.playbackMode === 'once' && timeRef.current >= scenario.duration) {
      timeRef.current = 0;
      frameRef.current = sampleScenario(scenario, 0);
      setTime(0);
    }
    let raf = 0;
    let previous = 0;
    let lastPublished = 0;
    const tick = (now: number) => {
      if (!document.hidden && playing) {
        if (previous) timeRef.current = advancePlaybackTime(timeRef.current, Math.min((now - previous) / 1000, 0.1) * speed, scenario.duration, scenario.playbackMode);
        frameRef.current = sampleScenario(scenario, timeRef.current);
        if (scenario.playbackMode === 'once' && timeRef.current >= scenario.duration) {
          setTime(scenario.duration);
          setPlaying(false);
          return;
        }
        if (now - lastPublished > 80) {
          setTime(timeRef.current);
          lastPublished = now;
        }
      }
      previous = document.hidden ? 0 : now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [scenario, playing, speed]);

  return { playing, setPlaying, speed, setSpeed, time, frameRef, seek };
}

export function advancePlaybackTime(time: number, delta: number, duration: number, mode: ScenarioBundle['playbackMode']): number {
  if (!Number.isFinite(duration) || duration <= 0) return 0;
  const next = Math.max(0, time + delta);
  return mode === 'once' ? Math.min(duration, next) : next % duration;
}
