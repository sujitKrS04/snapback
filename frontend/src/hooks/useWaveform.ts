import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 50;

export function useWaveform(
  track: MediaStreamTrack | null,
  bufferSize: number = 32,
): number[] {
  const [levels, setLevels] = useState<number[]>(() =>
    new Array(bufferSize).fill(0),
  );
  const bufRef = useRef<number[]>(new Array(bufferSize).fill(0));

  useEffect(() => {
    if (!track) {
      // Reset to flat when track is unavailable
      bufRef.current = new Array(bufferSize).fill(0);
      setLevels(bufRef.current);
      return;
    }

    let ctx: AudioContext | null = null;
    try {
      ctx = new AudioContext();
    } catch {
      return;
    }
    const source = ctx.createMediaStreamSource(new MediaStream([track]));
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.75;
    source.connect(analyser);

    const timeData = new Uint8Array(analyser.fftSize);
    let raf: number;
    let lastUpdate = 0;

    ctx.resume().catch(() => {});

    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (now - lastUpdate < POLL_INTERVAL_MS) return;
      lastUpdate = now;

      analyser.getByteTimeDomainData(timeData);
      let sum = 0;
      for (let i = 0; i < timeData.length; i++) {
        const v = (timeData[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / timeData.length);
      // Normalize: RMS 0..~0.7 for real speech → scale to 0..1
      const normalized = Math.min(1, rms * 3);

      bufRef.current = [...bufRef.current.slice(1), normalized];
      setLevels([...bufRef.current]);
    };

    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      source.disconnect();
      analyser.disconnect();
      ctx.close();
    };
  }, [track, bufferSize]);

  return levels;
}
