/** Desk-phone sounds, synthesised. No audio files to ship. */

let ringNodes: { ctx: AudioContext; timer: number } | null = null;

export function startRing() {
  stopRing();
  const ctx = new AudioContext();
  const ringOnce = () => {
    [0, 0.35].forEach((offset) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 440;
      gain.gain.value = 0.04;
      osc.connect(gain).connect(ctx.destination);
      const t = ctx.currentTime + offset;
      osc.start(t);
      osc.stop(t + 0.3);
    });
  };
  ringOnce();
  const timer = window.setInterval(ringOnce, 1800);
  ringNodes = { ctx, timer };
}

export function stopRing() {
  if (!ringNodes) return;
  clearInterval(ringNodes.timer);
  void ringNodes.ctx.close().catch(() => {});
  ringNodes = null;
}

export function playEnd() {
  const ctx = new AudioContext();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.setValueAtTime(620, ctx.currentTime);
  osc.frequency.exponentialRampToValueAtTime(180, ctx.currentTime + 0.25);
  gain.gain.value = 0.06;
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.3);
  osc.onended = () => void ctx.close().catch(() => {});
}
