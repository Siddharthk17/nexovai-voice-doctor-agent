import { useEffect, useRef } from "react";

type Props = {
  analyser: () => AnalyserNode | null;
  live: boolean;
};

/** Voice bars. Reads the live mic analyser, idles on a slow sine. */
export default function Waveform({ analyser, live }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let raf = 0;
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const data = new Uint8Array(128);
    let tick = 0;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const w = (canvas.width = canvas.clientWidth * 2);
      const h = (canvas.height = canvas.clientHeight * 2);
      ctx.clearRect(0, 0, w, h);
      tick += 1;
      const node = analyser();
      let levels: number[] = [];
      if (node && live) {
        node.getByteTimeDomainData(data);
        const step = Math.floor(data.length / 48);
        for (let i = 0; i < 48; i++) {
          const v = data[i * step] / 128 - 1;
          levels.push(Math.min(1, Math.abs(v) * 3));
        }
      } else {
        for (let i = 0; i < 48; i++) {
          levels.push(0.12 + 0.08 * Math.sin(tick / 22 + i / 4));
        }
      }
      const bw = w / 48;
      ctx.fillStyle = "#7fbdb9";
      levels.forEach((lv, i) => {
        const bh = Math.max(4, lv * h * 0.9);
        ctx.fillRect(i * bw + bw * 0.25, (h - bh) / 2, bw * 0.5, bh);
      });
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [analyser, live]);

  return <canvas ref={ref} className="wave" aria-hidden="true" />;
}
