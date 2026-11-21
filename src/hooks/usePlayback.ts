import { useCallback, useEffect, useRef, useState } from 'react';
import { sampleScenario } from '../scene/DemoScenarioProvider';
import type { ScenarioBundle } from '../types';

export function usePlayback(scenario: ScenarioBundle) {
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [time, setTime] = useState(0);
  const timeRef = useRef(0);
  const frameRef = useRef(sampleScenario(scenario, 0));
  const seek = useCallback((next: number) => {
    timeRef.current = Math.max(0, Math.min(scenario.duration, next));
    frameRef.current = sampleScenario(scenario, timeRef.current);
    setTime(timeRef.current);
  }, [scenario]);

  useEffect(() => {
    let raf = 0;
    let previous = 0;
    let lastPublished = 0;
    const tick = (now: number) => {
      if (!document.hidden && playing) {
        if (previous) timeRef.current = (timeRef.current + Math.min((now - previous) / 1000, 0.1) * speed) % scenario.duration;
        frameRef.current = sampleScenario(scenario, timeRef.current);
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
